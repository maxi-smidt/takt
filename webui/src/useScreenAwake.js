import { useEffect, useState } from "react";
import NoSleep from "nosleep.js";

export function useScreenAwake() {
  const [status, setStatus] = useState("pending");

  useEffect(() => {
    const noSleep = new NoSleep();
    let activating = false;
    let disposed = false;

    const removeActivationListeners = () => {
      document.removeEventListener("pointerdown", activate, true);
      document.removeEventListener("click", activate, true);
      document.removeEventListener("keydown", activate, true);
    };

    const activate = () => {
      if (disposed || activating || noSleep.isEnabled) return;
      activating = true;
      Promise.resolve(noSleep.enable())
        .then(() => {
          if (disposed) {
            noSleep.disable();
            return;
          }
          setStatus("active");
          removeActivationListeners();
        })
        .catch(() => {
          if (!disposed) setStatus("pending");
        })
        .finally(() => {
          activating = false;
        });
    };

    document.addEventListener("pointerdown", activate, true);
    document.addEventListener("click", activate, true);
    document.addEventListener("keydown", activate, true);

    // Native wake locks can normally be acquired immediately. The video-based
    // HTTP fallback may also autoplay; if blocked, the listeners above retry
    // during the first user interaction as required by mobile browsers.
    activate();

    return () => {
      disposed = true;
      removeActivationListeners();
      noSleep.disable();
    };
  }, []);

  return status;
}
