import type { ComponentType } from "react";
import { gridSizeToLogical, type IconName, type Rect } from "@janus/design";
import { isInput, type Widget, type WidgetType } from "@janus/schema";
import BarWidget from "./BarWidget";
import BigNumberWidget from "./BigNumberWidget";
import ButtonWidget from "./ButtonWidget";
import GaugeWidget from "./GaugeWidget";
import LineGraphWidget from "./LineGraphWidget";
import LogFeedWidget from "./LogFeedWidget";
import RingWidget from "./RingWidget";
import SliderWidget from "./SliderWidget";
import TemperatureWidget from "./TemperatureWidget";
import ToggleWidget from "./ToggleWidget";
import UnsupportedWidget from "./UnsupportedWidget";
import type { WidgetProps } from "./types";

/** Size in grid cells (columns x rows). */
export interface WidgetSize {
  w: number;
  h: number;
}

export interface WidgetDef {
  label: string;
  category: "Controls" | "Displays";
  icon: IconName;
  component: ComponentType<WidgetProps>;
  defaultSize: WidgetSize;
  /** Supported grid-snapping presets. Free positioning may use any valid size above the minimum. */
  sizes: readonly WidgetSize[];
}

const size = (w: number, h: number): WidgetSize => ({ w, h });

export const WIDGETS: Partial<Record<WidgetType, WidgetDef>> = {
  button: { label: "Button", category: "Controls", icon: "zap", component: ButtonWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1), size(2, 2)] },
  toggle: { label: "Toggle", category: "Controls", icon: "power", component: ToggleWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1)] },
  slider: { label: "Slider", category: "Controls", icon: "volume", component: SliderWidget, defaultSize: size(2, 1), sizes: [size(2, 1), size(4, 1)] },
  bar: { label: "Bar", category: "Displays", icon: "memory", component: BarWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1)] },
  gauge: { label: "Gauge", category: "Displays", icon: "cpu", component: GaugeWidget, defaultSize: size(2, 2), sizes: [size(1, 1), size(2, 1), size(2, 2)] },
  line: { label: "Line Graph", category: "Displays", icon: "monitor", component: LineGraphWidget, defaultSize: size(4, 1), sizes: [size(2, 1), size(4, 1), size(4, 2)] },
  ring: { label: "Ring", category: "Displays", icon: "refresh", component: RingWidget, defaultSize: size(2, 2), sizes: [size(1, 1), size(2, 2)] },
  temperature: { label: "Temperature", category: "Displays", icon: "thermometer", component: TemperatureWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1)] },
  number: { label: "Big Number", category: "Displays", icon: "terminal", component: BigNumberWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1)] },
  log: { label: "Log Feed", category: "Displays", icon: "terminal", component: LogFeedWidget, defaultSize: size(4, 2), sizes: [size(4, 2), size(4, 3)] },
};

export const WIDGET_TYPES = Object.keys(WIDGETS) as WidgetType[];

export function getWidgetDef(type: string): WidgetDef | undefined {
  return Object.hasOwn(WIDGETS, type) ? WIDGETS[type as WidgetType] : undefined;
}

/** Types we haven't built (or ones the backend invents) fall back to the Unsupported tile. */
export function getWidgetComponent(type: string): ComponentType<WidgetProps> {
  return getWidgetDef(type)?.component ?? UnsupportedWidget;
}

export function getWidgetMinimumSize(type: string): Pick<Rect, "w" | "h"> {
  const definition = getWidgetDef(type);
  if (!definition) return gridSizeToLogical(1, 1);
  const widths = definition.sizes.map(({ w, h }) => gridSizeToLogical(w, h).w);
  const heights = definition.sizes.map(({ w, h }) => gridSizeToLogical(w, h).h);
  return { w: Math.min(...widths), h: Math.min(...heights) };
}

export function snapWidgetSize(type: string, rect: Rect): Rect {
  const definition = getWidgetDef(type);
  if (!definition) return rect;
  const closest = definition.sizes
    .map(({ w, h }) => gridSizeToLogical(w, h))
    .reduce((best, candidate) => {
      const bestDistance = Math.hypot(rect.w - best.w, rect.h - best.h);
      const candidateDistance = Math.hypot(rect.w - candidate.w, rect.h - candidate.h);
      return candidateDistance < bestDistance ? candidate : best;
    });
  return { ...rect, ...closest };
}

export function createWidget(type: WidgetType, id: string, rect: Rect): Widget {
  const definition = getWidgetDef(type);
  if (!definition) throw new Error(`Unsupported widget type: ${type}.`);
  const base = { id, type, label: definition.label, icon: definition.icon, ...rect };
  return isInput(type) ? { ...base, type, action: "" } : { ...base, type, source: "" };
}
