/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the WebSocket URL (defaults to same-origin /ws). */
  readonly VITE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
