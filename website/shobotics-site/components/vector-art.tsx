export function SignalRail() {
  return <svg className="signal-rail" viewBox="0 0 130 10" aria-hidden="true"><line x1="0" y1="5" x2="130" y2="5"/><circle cx="18" cy="5" r="3"/><circle cx="84" cy="5" r="2"/><rect x="112" y="2" width="6" height="6"/></svg>;
}

export function OrganicField({ quiet = false }: { quiet?: boolean }) {
  const paths = Array.from({ length: 24 }, (_, i) => {
    const offset = i * 8;
    return `M ${70 + offset * 0.15} ${110 + offset} C ${160 - offset} ${20 + offset * 0.3}, ${270 + offset * 0.8} ${40 + offset}, ${310 - offset * 0.2} ${130 + offset * 0.4} C ${370 - offset * 0.1} ${250 - offset}, ${220 + offset * 0.1} ${360 + offset * 0.35}, ${120 + offset * 0.35} ${310 - offset * 0.15} C ${20 + offset * 0.5} ${260 - offset * 0.2}, ${20 + offset * 0.2} ${170 + offset * 0.15}, ${70 + offset * 0.15} ${110 + offset}`;
  });

  return (
    <svg className={quiet ? 'organic quiet' : 'organic'} viewBox="0 0 520 520" aria-hidden="true">
      <defs>
        <radialGradient id="violetGlow" cx="50%" cy="50%" r="50%"><stop offset="0" stopColor="#ca4dff" stopOpacity=".75"/><stop offset="1" stopColor="#ca4dff" stopOpacity="0"/></radialGradient>
      </defs>
      <g className="contours">{paths.map((d, i) => <path key={i} d={d}/>)}</g>
      <circle className="glow geo" cx="174" cy="322" r="42" fill="url(#violetGlow)"/>
      <circle className="node geo" cx="175" cy="320" r="4"/><circle className="node geo" cx="390" cy="88" r="3"/>
      <g className="orbit geo"><circle cx="260" cy="260" r="205"/><circle cx="260" cy="260" r="145"/><path d="M70 260h120M260 55v410"/><circle className="traveler" cx="80" cy="260" r="4"/></g>
      <g className="dotfield geo">{Array.from({ length: 56 }, (_, i) => <circle key={i} cx={52 + (i % 8) * 12} cy={56 + Math.floor(i / 8) * 12} r="1.2"/> )}</g>
      <g className="poly geo" transform="translate(105 410)"><polygon points="0,-38 34,-12 22,32 -22,32 -34,-12"/><path d="M0-38L22 32M34-12L-22 32M-34-12L34-12"/></g>
    </svg>
  );
}

export function DomainGlyph({ type, compact = false }: { type: 'mind' | 'machine' | 'media'; compact?: boolean }) {
  if (type === 'mind') return <MindGlyph compact={compact} />;
  if (type === 'machine') return <MachineGlyph compact={compact} />;
  return <MediaGlyph compact={compact} />;
}

function MindGlyph({ compact }: { compact?: boolean }) {
  return <svg className={compact ? 'glyph compact' : 'glyph'} viewBox="0 0 260 190" aria-hidden="true"><defs><radialGradient id="mindGlow"><stop offset="0" stopColor="#cc49ff" stopOpacity=".65"/><stop offset="1" stopColor="#cc49ff" stopOpacity="0"/></radialGradient></defs><circle cx="148" cy="92" r="50" fill="url(#mindGlow)"/><path className="draw" d="M118 42c-45 9-63 70-28 100 16 14 34 12 34 29h55c0-22 27-38 25-73-2-42-39-65-86-56z"/><path className="draw fine" d="M95 70c38-12 72 0 91 28M91 91c44-17 88-4 108 27M95 113c38-8 67 0 89 18M115 53c4 28-7 50-24 69M141 49c-3 33-12 64-39 91M167 58c-4 34-8 65-41 90"/><circle className="pulse" cx="194" cy="38" r="4"/><circle className="pulse" cx="219" cy="88" r="3"/><path className="orbit" d="M178 36C220 54 229 94 207 128"/></svg>;
}
function MachineGlyph({ compact }: { compact?: boolean }) {
  return <svg className={compact ? 'glyph compact' : 'glyph'} viewBox="0 0 260 190" aria-hidden="true"><circle className="glow-lite" cx="107" cy="114" r="37"/><path className="draw" d="M54 128c30-8 47-19 69-39 12-11 21-17 34-19l51-6M123 89l26 36c6 8 18 5 18-5l-1-41M145 79l38 43c7 8 20 2 17-9l-11-44M166 72l33 26c8 6 19-2 15-12l-8-19M83 108l34 42c9 11 26 3 23-11l-8-37"/><path className="draw fine" d="M40 139l36-15M62 151l48-20M90 161l32-23"/><circle className="pulse" cx="123" cy="89" r="4"/><circle className="pulse" cx="166" cy="72" r="3"/><polygon className="polyline" points="85,44 126,25 156,58 117,82"/></svg>;
}
function MediaGlyph({ compact }: { compact?: boolean }) {
  const waves = Array.from({ length: 11 }, (_, i) => `M20 ${95+i*2} C70 ${30+i*6}, 105 ${155-i*5}, 150 ${95+i*2} S220 ${65+i*3}, 250 ${95+i*2}`);
  return <svg className={compact ? 'glyph compact' : 'glyph'} viewBox="0 0 260 190" aria-hidden="true"><polygon className="plane" points="144,45 207,75 144,145"/><g>{waves.map((d,i)=><path key={i} className="wave" d={d}/>)}</g><circle className="pulse" cx="207" cy="75" r="4"/><circle className="pulse" cx="61" cy="104" r="3"/><path className="draw fine" d="M22 150h72M165 35h58M190 160h38"/></svg>;
}
