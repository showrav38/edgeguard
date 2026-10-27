/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_MQTT_URL?: string;
  readonly VITE_ACK_COOLDOWN_S?: string;
}
