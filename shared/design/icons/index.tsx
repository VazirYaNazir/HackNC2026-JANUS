import type { ComponentType } from "react";
import {
  Bell, Camera, ChevronLeft, ChevronRight, CircleHelp, Cpu, Folder, Lock, MemoryStick, Mic, Monitor, Moon, Music,
  Download, Ellipsis, Pause, Play, Plus, Power, RefreshCw, Save, ScanLine, Send, Share, SkipBack, SkipForward, Sparkles, Sun, Terminal,
  Thermometer, Volume2, VolumeX, Wifi, X, Zap,
  type LucideProps,
} from "lucide-react";

/** Curated icons for PC control. Widgets reference these by name (`widget.icon`). */
export const ICONS = {
  save: Save,
  back: ChevronLeft,
  chevron: ChevronRight,
  more: Ellipsis,
  scan: ScanLine,
  close: X,
  plus: Plus,
  share: Share,
  download: Download,
  lock: Lock,
  play: Play,
  pause: Pause,
  "skip-next": SkipForward,
  "skip-back": SkipBack,
  volume: Volume2,
  mute: VolumeX,
  mic: Mic,
  power: Power,
  send: Send,
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
