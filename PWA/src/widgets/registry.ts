import type { ComponentType } from "react";
import type { WidgetType } from "@janus/schema";
import BarWidget from "./BarWidget";
import ButtonWidget from "./ButtonWidget";
import GaugeWidget from "./GaugeWidget";
import SliderWidget from "./SliderWidget";
import ToggleWidget from "./ToggleWidget";
import UnsupportedWidget from "./UnsupportedWidget";
import type { WidgetProps } from "./types";

const registry: Partial<Record<WidgetType, ComponentType<WidgetProps>>> = {
  button: ButtonWidget,
  toggle: ToggleWidget,
  slider: SliderWidget,
  gauge: GaugeWidget,
  bar: BarWidget,
};

/** Types we haven't built (or ones the backend invents) fall back to the Unsupported tile. */
export function getWidgetComponent(type: string): ComponentType<WidgetProps> {
  return registry[type as WidgetType] ?? UnsupportedWidget;
}
