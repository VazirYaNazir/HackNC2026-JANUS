import type { ComponentType } from "react";
import type { WidgetType } from "@janus/schema";
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
  component: ComponentType<WidgetProps>;
  defaultSize: WidgetSize;
  /** Every size the widget is designed for. The builder should only offer these. */
  sizes: readonly WidgetSize[];
}

const size = (w: number, h: number): WidgetSize => ({ w, h });

export const WIDGETS: Partial<Record<WidgetType, WidgetDef>> = {
  button: { component: ButtonWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1), size(2, 2)] },
  toggle: { component: ToggleWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1)] },
  slider: { component: SliderWidget, defaultSize: size(2, 1), sizes: [size(2, 1), size(4, 1)] },
  bar: { component: BarWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1), size(4, 1)] },
  gauge: { component: GaugeWidget, defaultSize: size(2, 2), sizes: [size(1, 1), size(2, 1), size(2, 2)] },
  line: { component: LineGraphWidget, defaultSize: size(4, 1), sizes: [size(2, 1), size(4, 1), size(4, 2)] },
  ring: { component: RingWidget, defaultSize: size(2, 2), sizes: [size(1, 1), size(2, 2)] },
  temperature: { component: TemperatureWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1)] },
  number: { component: BigNumberWidget, defaultSize: size(2, 1), sizes: [size(1, 1), size(2, 1)] },
  log: { component: LogFeedWidget, defaultSize: size(4, 2), sizes: [size(4, 2), size(4, 3)] },
};

export const WIDGET_TYPES = Object.keys(WIDGETS) as WidgetType[];

export function getWidgetDef(type: string): WidgetDef | undefined {
  return Object.hasOwn(WIDGETS, type) ? WIDGETS[type as WidgetType] : undefined;
}

/** Types we haven't built (or ones the backend invents) fall back to the Unsupported tile. */
export function getWidgetComponent(type: string): ComponentType<WidgetProps> {
  return getWidgetDef(type)?.component ?? UnsupportedWidget;
}
