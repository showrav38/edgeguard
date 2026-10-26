"""Compare the learned models with the known rules that GENERATED the AI4I 2020 labels.

AI4I 2020 is semi-synthetic: failures were created by these published rules (UCI description):
  TWF  tool fails (or is replaced) at a random tool wear between 200 and 240 min
  HDF  (process temp - air temp) < 8.6 K  AND  speed < 1380 rpm
  PWF  power = torque * speed [rad/s] < 3500 W  OR  > 9000 W
  OSF  tool wear * torque > 11,000 (L) / 12,000 (M) / 13,000 (H) min*Nm
  RNF  0.1 % random, unpredictable

Questions answered on the TEST set (unseen by training and threshold choice):
  1. Do the labels follow the rules? (sanity check of our rule implementation)
  2. Does the risk model raise an alarm where a rule fires, and stay quiet where none fires?
  3. When it alarms inside a rule region, does the type model name that rule's failure type?
  4. How good would the rules alone be as a predictor? (an honest baseline for the ML model)

Outputs: docs/rules_check.png, models/model_info.json -> "rules_check"
Read-only with respect to the models and the threshold.
"""
import os

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score

import common as c

HDF_MAX_TEMP_DIFF_K = 8.6
HDF_MAX_SPEED_RPM = 1380
PWF_MIN_W, PWF_MAX_W = 3500, 9000
OSF_LIMIT = {0: 11000, 1: 12000, 2: 13000}  # by ProductType code L / M / H
TWF_WINDOW_MIN = (200, 240)


def derived_signals(df: pd.DataFrame) -> pd.DataFrame:
    """The physical quantities the rules are written in."""
    out = pd.DataFrame(index=df.index)
    out["temp_diff_k"] = df["ProcessTempK"] - df["AirTempK"]
    out["power_w"] = df["TorqueNm"] * df["SpeedRpm"] * 2 * np.pi / 60
    out["strain"] = df["ToolWearMin"] * df["TorqueNm"]
    out["osf_limit"] = df["ProductType"].map(OSF_LIMIT)
    return out


def rule_flags(df: pd.DataFrame) -> pd.DataFrame:
    d = derived_signals(df)
    return pd.DataFrame({
        # TWF is a window, not a trigger: inside it the tool MAY fail (or be replaced) at random.
        "TWF": df["ToolWearMin"].between(*TWF_WINDOW_MIN),
        "HDF": (d["temp_diff_k"] < HDF_MAX_TEMP_DIFF_K) & (df["SpeedRpm"] < HDF_MAX_SPEED_RPM),
        "PWF": (d["power_w"] < PWF_MIN_W) | (d["power_w"] > PWF_MAX_W),
        "OSF": d["strain"] > d["osf_limit"],
    })


def main():
    info = c.read_info()
    threshold = info["threshold"]
    test = pd.read_csv(os.path.join(c.DATA_DIR, "test.csv"))
    risk = joblib.load(os.path.join(c.MODELS_DIR, "risk.joblib"))
    ftype = joblib.load(os.path.join(c.MODELS_DIR, "failure_type.joblib"))

    x = c.X(test)
    p = risk.predict_proba(x)[:, 1]
    alarm = p >= threshold
    pred_type = np.array(c.FAILURE_TYPES)[ftype.predict(x)]
    rules = rule_flags(test)
    d = derived_signals(test)

    print(f"=== Rules vs labels vs model, test set ({len(test)} rows), threshold {threshold:.2f} ===")
    print(f"{'rule':4s} {'fires':>5s} {'label=1':>8s} {'model alarm':>12s} {'type match':>11s}")
    per_rule = {}
    for t in c.FAILURE_TYPES:
        fires = rules[t].to_numpy()
        n = int(fires.sum())
        labelled = int(test.loc[fires, t].sum())          # rows in the region with that failure label
        alarm_rate = float(alarm[fires].mean()) if n else float("nan")
        hit = fires & alarm
        type_match = float((pred_type[hit] == t).mean()) if hit.any() else float("nan")
        label_covered = int(test.loc[fires, t].sum()) / max(int(test[t].sum()), 1)
        per_rule[t] = {"fires": n, "labelled_in_region": labelled, "labels_inside_region": label_covered,
                       "model_alarm_rate": alarm_rate, "type_match_when_alarm": type_match}
        print(f"{t:4s} {n:5d} {labelled:8d} {alarm_rate:12.2f} {type_match:11.2f}"
              f"   ({label_covered:.0%} of {t} labels fall inside the rule region)")

    none = ~rules[["HDF", "PWF", "OSF"]].any(axis=1).to_numpy() & ~rules["TWF"].to_numpy()
    quiet_rate = float(alarm[none].mean())
    print(f"no rule region ({int(none.sum())} rows): model alarm rate {quiet_rate:.3f}")

    # Where do the false alarms come from? Inside the TWF window failure is a coin flip the model
    # cannot see, so with a 20:1 cost ratio alarming there is (nearly) the cheaper choice.
    false_alarm = alarm & (test["Machine failure"].to_numpy() == 0)
    fa_twf = int((false_alarm & rules["TWF"].to_numpy()).sum())
    print(f"false alarms: {int(false_alarm.sum())}, of which {fa_twf} inside the TWF window 200-240 min")

    # Baseline: alarm whenever a deterministic rule (HDF/PWF/OSF) fires. TWF is left out because
    # inside the window most tools are fine; adding it would flood the technician with alarms.
    y = test["Machine failure"].to_numpy()
    rule_pred = rules[["HDF", "PWF", "OSF"]].any(axis=1).to_numpy()
    baseline = {
        "rules_HDF_PWF_OSF": {"precision": float(precision_score(y, rule_pred)), "recall": float(recall_score(y, rule_pred))},
        "model": {"precision": float(precision_score(y, alarm)), "recall": float(recall_score(y, alarm))},
    }
    for k, m in baseline.items():
        print(f"baseline {k:18s}: precision={m['precision']:.3f} recall={m['recall']:.3f}")

    plot(test, d, p, alarm, threshold)
    c.write_info({"rules_check": {"per_rule": per_rule, "no_rule_alarm_rate": quiet_rate,
                                  "false_alarms": int(false_alarm.sum()), "false_alarms_in_twf_window": fa_twf,
                                  "baseline": baseline}})


def plot(test, d, p, alarm, threshold):
    fail = test["Machine failure"].to_numpy() == 1
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    def risk_scatter(ax, xv, title, xlabel, ftype):
        # Highlight only this panel's failure type; other failures are shown but muted.
        this = test[ftype].to_numpy() == 1
        other = fail & ~this
        ax.scatter(xv[~fail], p[~fail], s=6, c="#9aa5b1", alpha=0.5, label="no failure")
        ax.scatter(xv[other], p[other], s=12, c="#5c6bc0", alpha=0.6, label="other failure type")
        ax.scatter(xv[this], p[this], s=26, c="#c62828", edgecolors="k", linewidths=0.4, label=f"actual {ftype}")
        ax.axhline(threshold, color="#2e7d32", ls="--", lw=1, label=f"alarm threshold {threshold:.2f}")
        ax.set(title=title, xlabel=xlabel, ylabel="model failure risk", ylim=(-0.02, 1.02))

    # TWF: risk vs tool wear, with the 200-240 min failure window.
    ax = axes[0, 0]
    risk_scatter(ax, test["ToolWearMin"].to_numpy(), "TWF: risk vs tool wear", "tool wear [min]", "TWF")
    ax.axvspan(*TWF_WINDOW_MIN, color="#f9a825", alpha=0.2, label="TWF window 200-240 min")
    ax.legend(fontsize=8, loc="upper left")

    # HDF: a 2-D rule, so show the plane, coloured by the model's decision.
    ax = axes[0, 1]
    ax.scatter(d["temp_diff_k"][~alarm], test["SpeedRpm"][~alarm], s=6, c="#9aa5b1", alpha=0.5, label="no alarm")
    ax.scatter(d["temp_diff_k"][alarm], test["SpeedRpm"][alarm], s=10, c="#ef6c00", label="model alarm")
    hdf = test["HDF"].to_numpy() == 1
    ax.scatter(d["temp_diff_k"][hdf], test["SpeedRpm"][hdf], s=40, facecolors="none", edgecolors="k", label="actual HDF")
    ax.axvline(HDF_MAX_TEMP_DIFF_K, color="k", ls=":", lw=1)
    ax.axhline(HDF_MAX_SPEED_RPM, color="k", ls=":", lw=1)
    ax.fill_between([d["temp_diff_k"].min(), HDF_MAX_TEMP_DIFF_K], 1150, HDF_MAX_SPEED_RPM,
                    color="#f9a825", alpha=0.2, label="HDF rule region")
    ax.set(title="HDF: temp difference x speed", xlabel="process - air temperature [K]",
           ylabel="speed [rpm]", ylim=(1150, 2000))
    ax.legend(fontsize=8, loc="upper right")

    # PWF: risk vs mechanical power, with the two limits.
    ax = axes[1, 0]
    risk_scatter(ax, d["power_w"].to_numpy(), "PWF: risk vs power", "power = torque x speed [W]", "PWF")
    for v in (PWF_MIN_W, PWF_MAX_W):
        ax.axvline(v, color="k", ls=":", lw=1)
    ax.axvspan(d["power_w"].min(), PWF_MIN_W, color="#f9a825", alpha=0.2, label="PWF rule region")
    ax.axvspan(PWF_MAX_W, d["power_w"].max(), color="#f9a825", alpha=0.2)
    ax.legend(fontsize=8, loc="upper center")

    # OSF: risk vs tool wear x torque, limits depend on product type.
    ax = axes[1, 1]
    risk_scatter(ax, d["strain"].to_numpy(), "OSF: risk vs tool wear x torque", "tool wear x torque [min*Nm]", "OSF")
    for code, lim in OSF_LIMIT.items():
        ax.axvline(lim, color="k", ls=":", lw=1)
        ax.text(lim, 0.5, " LMH"[code + 1], fontsize=9, ha="right")
    ax.axvspan(min(OSF_LIMIT.values()), d["strain"].max(), color="#f9a825", alpha=0.2, label="OSF rule region (L/M/H limits)")
    ax.legend(fontsize=8, loc="upper left")

    fig.suptitle("Learned risk model vs the AI4I 2020 generation rules (test set)", fontsize=13)
    fig.tight_layout()
    fig.savefig(os.path.join(c.DOCS_DIR, "rules_check.png"), dpi=110)
    print("saved docs/rules_check.png")


if __name__ == "__main__":
    main()
