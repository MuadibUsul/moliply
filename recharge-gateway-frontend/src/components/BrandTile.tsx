import { Github } from "lucide-react";
import type { CSSProperties } from "react";

type TileSpec = {
  background: string;
  glow: string;
  glyph: (size: number) => React.ReactNode;
};

function Asterisk({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      {[0, 45, 90, 135].map((angle) => (
        <rect
          key={angle}
          x="14.6"
          y="2.5"
          width="2.8"
          height="27"
          rx="1.4"
          fill="currentColor"
          transform={`rotate(${angle} 16 16)`}
        />
      ))}
    </svg>
  );
}

function Diamond({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="8.5" y="8.5" width="15" height="15" rx="2.6" transform="rotate(45 16 16)" stroke="currentColor" strokeWidth="2.4" />
    </svg>
  );
}

function Wave({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M4 20c3.8 0 5.6-6 9.4-6s5.6 6 9.4 6 3.8-3 5.2-3" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

const TILES: Record<string, TileSpec> = {
  gpt: {
    background: "linear-gradient(145deg, #2dd4bf 0%, #0d9488 55%, #0f766e 100%)",
    glow: "rgba(45, 212, 191, 0.34)",
    glyph: (size) => (
      <span style={{ fontSize: size * 0.3 }} className="font-display font-bold tracking-tight text-white">
        GPT
      </span>
    ),
  },
  claude: {
    background: "linear-gradient(145deg, #ff9a76 0%, #f9734b 48%, #e8553b 100%)",
    glow: "rgba(249, 115, 75, 0.36)",
    glyph: (size) => (
      <span className="text-white">
        <Asterisk size={size * 0.46} />
      </span>
    ),
  },
  cursor: {
    background: "linear-gradient(145deg, #a78bfa 0%, #7c3aed 55%, #4c1d95 100%)",
    glow: "rgba(124, 58, 237, 0.36)",
    glyph: (size) => (
      <span className="text-white/95">
        <Diamond size={size * 0.48} />
      </span>
    ),
  },
  midjourney: {
    background: "linear-gradient(145deg, #60a5fa 0%, #2563eb 55%, #1e3a8a 100%)",
    glow: "rgba(37, 99, 235, 0.36)",
    glyph: (size) => (
      <span className="text-white/95">
        <Wave size={size * 0.52} />
      </span>
    ),
  },
  github: {
    background: "linear-gradient(145deg, #a78bfa 0%, #6d28d9 55%, #2e1065 100%)",
    glow: "rgba(109, 40, 217, 0.36)",
    glyph: (size) => <Github size={size * 0.44} className="text-white" strokeWidth={2.2} />,
  },
  notion: {
    background: "linear-gradient(145deg, #475569 0%, #1e293b 60%, #0f172a 100%)",
    glow: "rgba(71, 85, 105, 0.32)",
    glyph: (size) => (
      <span style={{ fontSize: size * 0.4 }} className="font-display font-bold text-white">
        N
      </span>
    ),
  },
  api: {
    background: "linear-gradient(145deg, #22d3ee 0%, #0891b2 55%, #164e63 100%)",
    glow: "rgba(34, 211, 238, 0.32)",
    glyph: (size) => (
      <span style={{ fontSize: size * 0.27 }} className="font-mono font-bold tracking-tighter text-white">
        {"{}"}
      </span>
    ),
  },
  default: {
    background: "linear-gradient(145deg, #60a5fa 0%, #2563eb 55%, #172554 100%)",
    glow: "rgba(37, 99, 235, 0.34)",
    glyph: (size) => (
      <span style={{ fontSize: size * 0.34 }} className="font-display font-bold text-white">
        M
      </span>
    ),
  },
};

export function BrandTile({
  logoKey,
  size = 56,
  className = "",
  style,
}: {
  logoKey: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  const spec = TILES[logoKey] || TILES.default;
  // Callers position hero tiles with `absolute`; a hard-coded `relative` would
  // win the cascade and collapse the four floating cards onto each other.
  const positionClass = /(^|\s)(absolute|fixed|sticky)(\s|$)/.test(className) ? "" : "relative";
  return (
    <span
      className={`${positionClass} inline-flex shrink-0 items-center justify-center overflow-hidden ${className}`}
      style={{
        width: size,
        height: size,
        borderRadius: Math.max(12, size * 0.27),
        background: spec.background,
        boxShadow: `0 18px 50px ${spec.glow}, inset 0 1px 0 rgba(255,255,255,0.28)`,
        ...style,
      }}
      aria-hidden="true"
    >
      <span className="absolute inset-0 bg-[radial-gradient(circle_at_28%_12%,rgba(255,255,255,0.4),transparent_42%)]" />
      <span className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/20 to-transparent" />
      <span className="relative">{spec.glyph(size)}</span>
    </span>
  );
}
