import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

/**
 * A "key phrase" card laid over a SHARP picture: one exact sentence of the
 * script, in a translucent box, wrapped between words only.
 *
 * Unlike HeroTitle it never splits the text into letters (a split can break a
 * word at a line end and alter a quotation) and its size follows the text
 * length, so a whole sentence fits. The picture behind stays visible.
 */
type KeyPhraseCardProps = {
  text: string;
  textColor?: string;
  boxColor?: string;
  accentColor?: string;
  position?: "center" | "bottom";
  fontFamily?: string;
};

export const KeyPhraseCard: React.FC<KeyPhraseCardProps> = ({
  text,
  textColor = "#F8FAFC",
  boxColor = "rgba(15,23,42,0.72)",
  accentColor = "#22D3EE",
  position = "center",
  fontFamily,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const fade = Math.min(Math.round(fps * 0.3), Math.floor(durationInFrames / 3));
  const opacity = interpolate(
    frame,
    [0, fade, durationInFrames - fade, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const short = Math.min(width, height);
  // ~9 % of the short side for a few words, down to ~5 % for a long sentence
  const scale = text.length <= 40 ? 0.09 : text.length <= 90 ? 0.07 : 0.055;
  const fontSize = Math.round(short * scale);

  return (
    <AbsoluteFill
      style={{
        justifyContent: position === "bottom" ? "flex-end" : "center",
        alignItems: "center",
        paddingBottom: position === "bottom" ? Math.round(height * 0.12) : 0,
        opacity,
      }}
    >
      <div
        style={{
          maxWidth: "82%",
          padding: `${Math.round(fontSize * 0.55)}px ${Math.round(fontSize * 0.8)}px`,
          background: boxColor,
          borderLeft: `${Math.max(4, Math.round(fontSize * 0.12))}px solid ${accentColor}`,
          borderRadius: Math.round(fontSize * 0.25),
        }}
      >
        <p
          style={{
            margin: 0,
            color: textColor,
            fontSize,
            lineHeight: 1.25,
            fontWeight: 700,
            fontFamily,
            textAlign: "left",
            whiteSpace: "normal",
            wordBreak: "normal",
            overflowWrap: "normal",
            hyphens: "manual",
            textShadow: "0 2px 6px rgba(0,0,0,0.45)",
          }}
        >
          {text}
        </p>
      </div>
    </AbsoluteFill>
  );
};
