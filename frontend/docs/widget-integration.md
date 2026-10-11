# Widget integration audit

Studio and the PWA render the same 17 widget families through `@janus/widgets`.
Palette defaults now bind each native display to a real publisher and each supported continuous native control to its actual action. Saved bindings remain unchanged.

| Widget | Default or available integration | Runtime requirements |
| --- | --- | --- |
| Slider, knob | Volume; optional signed scroll amount | Native tools enabled; volume 0–100, scroll −100–100 integer |
| Joystick | Mouse movement, Y positive upwards | Native tools enabled; macOS Accessibility permission |
| Button | Left/right click selectable in Inspector | Choose the action and Save & Push; native tools enabled |
| Toggle, D-pad | Explicit generated/custom action | Configure with the AI assistant, Gemini key, approved local policy, installed isolation adapter |
| Command, Agent | Typed text sent into the configured generation request | Same generated-program requirements; Agent displays request status/reply in both clients |
| Gauge, line graph | CPU percentage | Native metrics enabled |
| Bar, ring | RAM percentage | Native metrics enabled |
| Big number | RAM used in MB | Native metrics enabled |
| Temperature | CPU temperature | Native metrics enabled; supported hardware sensor. Missing sensor status is shown |
| Process tree | Relevant apps exceeding native CPU/RAM thresholds, with ancestors | Native process inspection enabled; inspection failures are shown |
| Log feed | Native metrics status; other native status streams selectable | A native publisher or explicitly configured custom backend publisher |
| Stream / screen mirror | Bounded JPEG frames from `screen.frame` | Screen sharing opted into locally; macOS Screen Recording permission or available Windows desktop session |

The mirror reports LIVE only after the browser decodes a frame. Capture and permission failures use `native.screen.status`, clear the image, and recover when a valid new frame arrives. Disconnecting clears live readings in both clients. Frames are not stored in the panel cache.

Inspector limits built-in source choices to compatible data types: image frames for streams, structured process data for process trees, text statuses for logs, numbers for numeric displays. Custom sources require an actual backend publisher. Unconfigured generated controls show setup guidance before opening an execution request.

The Studio assistant accepts source-generation prompts without selecting a widget or connecting the laptop agent. A backend Gemini key is required. If no control is selected, or the laptop cannot validate and execute safely, Generate creates a saved C draft for review and download. It does not change widget bindings or dispatch a laptop job. Saved drafts can be suggested for explicit reuse on a later execution request.

When a control is selected and both execution boundaries are available, the assistant configures the control and opens request review. Running it still requires fresh local validation and approval. Generated C execution needs a separately installed isolation adapter; startup authorization does not supply one. All widget renderers exist, but arbitrary generated programs and hardware-specific temperature sensors cannot be claimed to execute on an unconfigured laptop.

Validation covers registry defaults for every family, edit/live rendering, type-compatible source selection, native ranges, mouse and volume actions, generated reply propagation, permission errors and frame decode/recovery. The browser workflow pairs a phone through the real backend, sends native controls, mirrors an actual JPEG in Studio and PWA, and reloads the phone to check the current frame replay.
