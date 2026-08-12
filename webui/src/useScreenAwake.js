import { useEffect, useState } from "react";
import NoSleep from "nosleep.js";

export function useScreenAwake() {
  const [status, setStatus] = useState("pending");

  useEffect(() => {
    const nativeWakeLock = "wakeLock" in navigator;
    const noSleep = nativeWakeLock ? null : new NoSleep();
    const fallbackVideo = noSleep?.noSleepVideo || null;
    let wakeLock = null;
    let active = false;
    let activating = false;
    let retryRequested = false;
    let everActivated = false;
    let disposed = false;

    const updateStatus = (nextStatus) => {
      if (!disposed) setStatus(nextStatus);
    };

    const activate = async () => {
      if (disposed || document.visibilityState !== "visible" || active) return;
      if (activating) {
        retryRequested = true;
        return;
      }

      activating = true;
      try {
        if (nativeWakeLock) {
          const sentinel = await navigator.wakeLock.request("screen");
          wakeLock = sentinel;
          sentinel.addEventListener("release", () => {
            if (wakeLock === sentinel) wakeLock = null;
            active = false;
            updateStatus("pending");
            if (!disposed && document.visibilityState === "visible") {
              queueMicrotask(activate);
            }
          });
        } else {
          await noSleep.enable();
        }

        if (disposed || document.visibilityState !== "visible") {
          if (nativeWakeLock && wakeLock && !wakeLock.released) {
            wakeLock.release().catch(() => {});
          } else if (noSleep) {
            noSleep.disable();
          }
          return;
        }

        active = true;
        everActivated = true;
        updateStatus("active");
      } catch {
        active = false;
        updateStatus("pending");
      } finally {
        activating = false;
        if (retryRequested) {
          retryRequested = false;
          if (!active) queueMicrotask(activate);
        }
      }
    };

    const deactivate = () => {
      active = false;
      updateStatus("pending");
      if (wakeLock && !wakeLock.released) {
        const sentinel = wakeLock;
        wakeLock = null;
        sentinel.release().catch(() => {});
      }
      if (noSleep) noSleep.disable();
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") {
        retryRequested = false;
        deactivate();
      } else if (nativeWakeLock || everActivated) {
        activate();
      }
    };

    const handleFallbackPause = () => {
      if (disposed || document.visibilityState !== "visible") return;
      active = false;
      updateStatus("pending");
      queueMicrotask(activate);
    };

    document.addEventListener("click", activate, true);
    document.addEventListener("keydown", activate, true);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    if (fallbackVideo) {
      fallbackVideo.className = "screen-awake-media";
      fallbackVideo.setAttribute("aria-hidden", "true");
      fallbackVideo.addEventListener("pause", handleFallbackPause);
      document.body.appendChild(fallbackVideo);
    } else {
      // The native API does not require a gesture, so request it immediately.
      activate();
    }

    return () => {
      disposed = true;
      document.removeEventListener("click", activate, true);
      document.removeEventListener("keydown", activate, true);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (fallbackVideo) {
        fallbackVideo.removeEventListener("pause", handleFallbackPause);
        fallbackVideo.remove();
      }
      deactivate();
    };
  }, []);

  return status;
}
