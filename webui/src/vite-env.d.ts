/// <reference types="vite/client" />

interface Window {
  webkitAudioContext?: typeof AudioContext;
}

interface Navigator {
  wakeLock?: WakeLock;
}

interface WakeLock {
  request(type: "screen"): Promise<WakeLockSentinel>;
}

interface WakeLockSentinel extends EventTarget {
  readonly released: boolean;
  release(): Promise<void>;
}
