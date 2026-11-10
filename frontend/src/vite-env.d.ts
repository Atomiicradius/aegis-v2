/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the cloud stats API (default http://localhost:8010). */
  readonly VITE_CLOUD_API?: string;
}
