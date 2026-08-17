import { useState } from "react";
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  Dialog,
  DialogActions,
  DialogBody,
  Field,
  Select,
  Stack,
  TextInput,
} from "../shared/ui";

type Theme = "timer" | "fleet";

function ButtonsSection() {
  return (
    <section className="gallery-section">
      <h2>Buttons</h2>
      <p>webui/src/shared/ui/Button.tsx, IconButton.tsx</p>
      <div className="gallery-row">
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger">Danger</Button>
        <Button variant="primary" size="sm">
          Small
        </Button>
        <Button variant="primary" loading>
          Loading
        </Button>
        <Button variant="primary" disabled>
          Disabled
        </Button>
      </div>
    </section>
  );
}

function FormSection() {
  const [text, setText] = useState("");
  const [release, setRelease] = useState("stable");
  const [checked, setChecked] = useState(false);

  return (
    <section className="gallery-section">
      <h2>Form controls</h2>
      <p>webui/src/shared/ui/Field.tsx, TextInput.tsx, Select.tsx, Checkbox.tsx</p>
      <div className="gallery-card">
        <Stack gap="16px">
          <Field label="Device name" hint="Shown across the fleet portal">
            {(fieldProps) => <TextInput {...fieldProps} value={text} onChange={(event) => setText(event.target.value)} />}
          </Field>
          <Field label="Device name" error="Required">
            {(fieldProps) => <TextInput {...fieldProps} value="" onChange={() => {}} />}
          </Field>
          <Field label="Release channel">
            {(fieldProps) => (
              <Select
                {...fieldProps}
                value={release}
                onValueChange={setRelease}
                options={[
                  { value: "stable", label: "Stable" },
                  { value: "beta", label: "Beta" },
                  { value: "archived", label: "Archived", disabled: true },
                ]}
              />
            )}
          </Field>
          <Checkbox checked={checked} onCheckedChange={setChecked}>
            Interrupt the run anyway
          </Checkbox>
        </Stack>
      </div>
    </section>
  );
}

function BadgesSection() {
  return (
    <section className="gallery-section">
      <h2>Badges</h2>
      <p>webui/src/shared/ui/Badge.tsx</p>
      <div className="gallery-row">
        <Badge>Neutral</Badge>
        <Badge tone="accent">v0.5.1</Badge>
        <Badge tone="success">Online</Badge>
        <Badge tone="warning">Recovery</Badge>
        <Badge tone="danger">Offline</Badge>
      </div>
    </section>
  );
}

function CalloutsSection() {
  return (
    <section className="gallery-section">
      <h2>Callouts</h2>
      <p>webui/src/shared/ui/Callout.tsx</p>
      <Stack gap="10px">
        <Callout tone="info">The agent re-checks device state immediately before acting.</Callout>
        <Callout tone="warning">This Pi's Fleet agent does not offer this capability yet.</Callout>
        <Callout tone="danger">This Pi is not idle — continuing will interrupt a running or unsaved run.</Callout>
      </Stack>
    </section>
  );
}

function DialogSection() {
  const [open, setOpen] = useState(false);
  return (
    <section className="gallery-section">
      <h2>Dialog</h2>
      <p>webui/src/shared/ui/Dialog.tsx — powers fleet/components/Modal.tsx</p>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Open dialog
      </Button>
      {open && (
        <Dialog title="Restart Pi · lehrgang-3" eyebrow="Confirm maintenance" onClose={() => setOpen(false)}>
          <DialogBody>
            <p style={{ margin: 0 }}>You are about to restart the TAKT service on lehrgang-3.</p>
          </DialogBody>
          <DialogActions>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => setOpen(false)}>
              Restart
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </section>
  );
}

export function Gallery() {
  const [theme, setTheme] = useState<Theme>("fleet");

  return (
    <div className={`gallery theme-${theme}`}>
      <header className="gallery-header">
        <h1>TAKT UI Gallery</h1>
        <div className="gallery-theme-toggle" role="group" aria-label="Preview palette">
          <button type="button" aria-pressed={theme === "timer"} onClick={() => setTheme("timer")}>
            Timer (blue)
          </button>
          <button type="button" aria-pressed={theme === "fleet"} onClick={() => setTheme("fleet")}>
            Fleet (green)
          </button>
        </div>
      </header>
      <ButtonsSection />
      <FormSection />
      <BadgesSection />
      <CalloutsSection />
      <DialogSection />
    </div>
  );
}
