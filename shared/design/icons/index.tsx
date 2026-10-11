import type { ComponentType } from "react";
import {
  Bell, Camera, CircleHelp, Cpu, Folder, Lock, MemoryStick, Mic, Monitor, Moon, Music,
  Grid3X3, MousePointer2, Pause, Play, Power, RefreshCw, Save, Search, SkipBack, SkipForward,
  SlidersHorizontal, Smartphone, Sparkles, Sun, Terminal,
  Thermometer, Volume2, VolumeX, Wifi, Zap,
  type LucideProps,
} from "lucide-react";

/** Curated icons for PC control. Widgets reference these by name (`widget.icon`). */
export const ICONS = {
  save: Save,
  lock: Lock,
  play: Play,
  pause: Pause,
  "skip-next": SkipForward,
  "skip-back": SkipBack,
  volume: Volume2,
  mute: VolumeX,
  mic: Mic,
  power: Power,
  refresh: RefreshCw,
  camera: Camera,
  sparkle: Sparkles,
  cpu: Cpu,
  memory: MemoryStick,
  wifi: Wifi,
  thermometer: Thermometer,
  music: Music,
  monitor: Monitor,
  terminal: Terminal,
  folder: Folder,
  zap: Zap,
  moon: Moon,
  sun: Sun,
  bell: Bell,
  help: CircleHelp,
  search: Search,
  settings: SlidersHorizontal,
  grid: Grid3X3,
  phone: Smartphone,
  edit: MousePointer2,
} satisfies Record<string, ComponentType<LucideProps>>;

export type IconName = keyof typeof ICONS;
export const ICON_NAMES = Object.keys(ICONS) as IconName[];

export interface IconProps extends Omit<LucideProps, "ref"> {
  /** Unknown names (including "asset:<id>") render the help icon. */
  name: string;
  size?: number;
}

export function Icon({ name, size = 20, ...rest }: IconProps) {
  const Glyph = Object.hasOwn(ICONS, name) ? ICONS[name as IconName] : ICONS.help;
  return <Glyph size={size} aria-hidden="true" {...rest} />;
}
