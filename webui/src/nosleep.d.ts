declare module "nosleep.js" {
  export default class NoSleep {
    readonly noSleepVideo?: HTMLVideoElement;
    enable(): Promise<void>;
    disable(): void;
  }
}
