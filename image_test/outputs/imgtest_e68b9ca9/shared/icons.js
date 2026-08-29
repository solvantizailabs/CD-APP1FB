// =============================================================
// HyperFrames Shared Icon Library
// Curated, self-contained set of 24x24 line-style SVG icons used to give
// every template (not just illustrated_scene) real pictorial content next
// to text, instead of shapes/boxes containing only labels. Each entry is
// the INNER markup of an <svg viewBox="0 0 24 24"> - no <svg> wrapper, no
// fill/stroke color baked in (callers apply the "theme-stroke" class so
// runtime theming colors it consistently with the rest of the engine).
//
// Keep icons stroke-based (fill="none", strokeWidth ~2, strokeLinecap/join
// "round") to match the existing icon-card / timeline-icon visual language
// already used by TitleSlide/HorizontalTimeline.
// =============================================================

var HFIcons = {
  // --- nature / science ---
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
  water_drop: '<path d="M12 2s7 8.5 7 13a7 7 0 0 1-14 0c0-4.5 7-13 7-13z"/>',
  cloud: '<path d="M6.5 19a4.5 4.5 0 0 1-.5-8.98A6 6 0 0 1 17.6 8.03 4.5 4.5 0 0 1 17 19H6.5z"/>',
  rain: '<path d="M6.5 15a4.5 4.5 0 0 1-.5-8.98A6 6 0 0 1 17.6 4.03 4.5 4.5 0 0 1 17 15H6.5z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
  wave: '<path d="M2 12c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/><path d="M2 17c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/>',
  ocean_wave: '<path d="M2 8c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/><path d="M2 13c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/><path d="M2 18c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/>',
  water_tap: '<path d="M4 6h9a4 4 0 0 1 4 4v2"/><circle cx="4" cy="6" r="1.4"/><path d="M12 14v2.5"/><path d="M12 16.5a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4z"/>',
  leaf: '<path d="M11 20A7 7 0 0 1 4 13c0-6 7-11 7-11s7 5 7 11a7 7 0 0 1-7 7z"/><path d="M11 20v-9"/>',
  tree: '<path d="M12 22v-6"/><path d="M12 16c-4 0-6-2.5-6-5.5C6 6.5 12 2 12 2s6 4.5 6 8.5c0 3-2 5.5-6 5.5z"/>',
  flower: '<circle cx="12" cy="12" r="2.5"/><path d="M12 2a3 3 0 0 1 3 3 3 3 0 0 1-3 3 3 3 0 0 1-3-3 3 3 0 0 1 3-3zM12 16a3 3 0 0 1 3 3 3 3 0 0 1-3 3 3 3 0 0 1-3-3 3 3 0 0 1 3-3zM4 12a3 3 0 0 1 3-3 3 3 0 0 1 3 3 3 3 0 0 1-3 3 3 3 0 0 1-3-3zM14 12a3 3 0 0 1 3-3 3 3 0 0 1 3 3 3 3 0 0 1-3 3 3 3 0 0 1-3-3z"/>',
  mountain: '<path d="M3 20l6-11 4 6 3-4 5 9H3z"/>',
  wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h9a2.5 2.5 0 1 1-2.5 2.5"/>',
  fire: '<path d="M12 22a6 6 0 0 0 6-6c0-3-2-5-3-7-.5 2-1.5 3-2.5 3C13 9 13 5 10.5 2 10 5 7 7 7 11a5 5 0 0 0 0 5 6 6 0 0 0 5 6z"/>',
  snowflake: '<path d="M12 2v20M4.2 7l15.6 10M4.2 17L19.8 7"/>',
  seed: '<path d="M12 2C7 2 4 7 4 12s3 10 8 10 8-5 8-10-3-10-8-10z"/><path d="M12 6v12"/>',
  atom: '<circle cx="12" cy="12" r="1.5"/><ellipse cx="12" cy="12" rx="10" ry="4.2"/><ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(120 12 12)"/>',
  molecule: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="17" r="2.5"/><path d="M8 7.5l3 8M16 7.5l-3 8M8.5 6h7"/>',

  // --- body / anatomy ---
  heart: '<path d="M12 21s-7.5-5-10-9.5C0.3 7.8 2.5 4 6.2 4c2 0 3.6 1.2 4.8 3 1.2-1.8 2.8-3 4.8-3 3.7 0 5.9 3.8 4.2 7.5C19.5 16 12 21 12 21z"/>',
  brain: '<path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5h1a3 3 0 0 0 3-3V6a3 3 0 0 0-1-3z"/><path d="M15 3a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5h-1a3 3 0 0 1-3-3V6a3 3 0 0 1 1-3z"/>',
  lungs: '<path d="M12 3v9"/><path d="M12 12c-1-3-3-4-5-4-2.5 0-4 2-4 5 0 3 1.5 6 4 6 1.5 0 3-1 3.5-3"/><path d="M12 12c1-3 3-4 5-4 2.5 0 4 2 4 5 0 3-1.5 6-4 6-1.5 0-3-1-3.5-3"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="3"/>',
  dna: '<path d="M6 3c0 6 12 12 12 18M18 3c0 6-12 12-12 18"/><path d="M7.5 7h9M6 12h12M7.5 17h9"/>',
  bone: '<path d="M5 9a2.5 2.5 0 1 1 3.5 2.3l7.2 7.2A2.5 2.5 0 1 1 18 21l-7.2-7.2A2.5 2.5 0 1 1 8.5 15l-2-2A2.5 2.5 0 0 1 5 9z"/>',

  // --- concepts / general ---
  lightbulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.6.6 1 1.4 1 2.5h6c0-1.1.4-1.9 1-2.5A6 6 0 0 0 12 3z"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16z"/><path d="M4 19a2.5 2.5 0 0 1 2.5-2.5H20"/>',
  pencil: '<path d="M17 3l4 4L7 21H3v-4L17 3z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  flag: '<path d="M5 3v18"/><path d="M5 4h13l-3 4 3 4H5"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0V4z"/><path d="M8 5H5a3 3 0 0 0 3 4M16 5h3a3 3 0 0 1-3 4"/><path d="M12 13v4M9 21h6M10 17h4v4h-4z"/>',
  star: '<path d="M12 2l3 7h7l-5.5 4.2L18.5 21 12 16.5 5.5 21l2-7.8L2 9h7z"/>',
  check: '<path d="M4 12l6 6L20 6"/>',
  cross: '<path d="M5 5l14 14M19 5L5 19"/>',
  arrow_up: '<path d="M12 20V4M5 11l7-7 7 7"/>',
  arrow_down: '<path d="M12 4v16M19 13l-7 7-7-7"/>',
  arrow_right: '<path d="M4 12h16M13 5l7 7-7 7"/>',
  arrow_left: '<path d="M20 12H4M11 19l-7-7 7-7"/>',
  cycle: '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  map_pin: '<path d="M12 22s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  home: '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
  factory: '<path d="M3 21V11l5 3v-3l5 3V8l6 4v9H3z"/>',
  coin: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2 0 0 1 5 0c0 1.5-2.5 2-2.5 3.5M12 16v1"/>',
  scale: '<path d="M12 3v18M5 7l7-4 7 4"/><path d="M3 7h6M15 7h6"/><path d="M3 7l-2 6a4 4 0 0 0 8 0l-2-6zM21 7l-2 6a4 4 0 0 0 8 0l-2-6z"/>',
  people: '<circle cx="8" cy="8" r="3"/><path d="M2 20a6 6 0 0 1 12 0"/><circle cx="17" cy="9" r="2.5"/><path d="M14.5 20a5 5 0 0 1 7.5-4.3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  message: '<path d="M4 4h16v12H8l-4 4V4z"/>',
  shield: '<path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3z"/>',
  key: '<circle cx="8" cy="14" r="4"/><path d="M11 11l9-9M17 5l3 3M14 8l2 2"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  chart_bar: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  chart_line: '<path d="M3 17l5-5 4 4 8-9"/><path d="M2 21h20"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  magnet: '<path d="M6 4h5v9a3.5 3.5 0 0 0 7 0V4h-5"/><path d="M6 4v9a3.5 3.5 0 0 1 0 0"/><path d="M6 8H2M22 8h-4"/>',
  battery: '<rect x="2" y="8" width="17" height="8" rx="1.5"/><path d="M21 11v2"/><path d="M6 11v2"/>',
  wire: '<path d="M3 12h4l2-4 4 8 2-4h6"/><circle cx="3" cy="12" r="1.3"/><circle cx="21" cy="12" r="1.3"/>',
  zap: '<path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/>',
  ruler: '<rect x="3" y="9" width="18" height="6" rx="1"/><path d="M7 9v2.5M11 9v3M15 9v2.5M19 9v3"/>',
  filter: '<path d="M4 4h16l-6.5 8v6l-3 2v-8z"/>',
  rocket: '<path d="M12 2s5 3 5 9-5 11-5 11-5-8-5-11 5-9 5-9z"/><circle cx="12" cy="10" r="2"/><path d="M8 17l-3 3M16 17l3 3"/>',
  plane: '<path d="M3 12l18-7-7 18-2-8-8-2 -1-1z"/>',
  car: '<path d="M4 16V11l2-5h12l2 5v5"/><path d="M4 16h16"/><circle cx="7.5" cy="17.5" r="1.5"/><circle cx="16.5" cy="17.5" r="1.5"/>',
  boat: '<path d="M3 15h18l-2 5H5l-2-5z"/><path d="M6 15V6h6l4 9"/><path d="M6 6h0"/>',
  paw: '<circle cx="7" cy="7" r="1.7"/><circle cx="12" cy="5" r="1.7"/><circle cx="17" cy="7" r="1.7"/><path d="M12 12a5 5 0 0 1 5 5c0 2-2 3-5 3s-5-1-5-3a5 5 0 0 1 5-5z"/>',
  fish: '<path d="M3 12s4-5 11-5 7 5 7 5-1 5-7 5-11-5-11-5z"/><circle cx="17" cy="10.5" r="0.6"/><path d="M3 12l-2-3M3 12l-2 3"/>',
  bird: '<path d="M4 12c3-4 8-6 12-4 2 1 4 3 4 3l-4 1 1 3-5-1-3 3-1-3-4-2z"/>',
  bug: '<circle cx="12" cy="13" r="5"/><path d="M9 8V6M15 8V6M6 11l-2-1M18 11l2-1M6 15l-2 1M18 15l2 1M12 8v10"/>',
  hourglass: '<path d="M6 2h12M6 22h12"/><path d="M7 2c0 5 5 6 5 10s-5 5-5 10M17 2c0 5-5 6-5 10s5 5 5 10"/>',
  wave_hand: '<path d="M8 12V6a1.5 1.5 0 0 1 3 0v5M11 11V4a1.5 1.5 0 0 1 3 0v7M14 11V6a1.5 1.5 0 0 1 3 0v6M17 12v-3a1.5 1.5 0 0 1 3 0v6a6 6 0 0 1-6 6h-2a6 6 0 0 1-5-2.7L4 14"/>',
  handshake: '<path d="M2 12l4-4h4l2 2 2-2h4l4 4"/><path d="M8 12l3 3 2-2 2 2 3-3"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 1.7-2.5 2-2.5 4"/><path d="M12 17v.01"/>',
  bell: '<path d="M6 10a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M15 9l-2 6-6 2 2-6 6-2z"/>',

  // --- physics ---
  force_arrow: '<path d="M4 12h13M13 6l6 6-6 6"/><circle cx="4" cy="12" r="1.5"/>',
  pulley: '<circle cx="12" cy="7" r="4"/><path d="M6 7H3M18 7h3M8 11l-3 9M16 11l3 9M8 20h8"/>',
  lever: '<path d="M2 16l20-6"/><circle cx="12" cy="13" r="1.6"/><path d="M12 13v7"/>',
  spring: '<path d="M4 12c2-4 4 4 6 0s4 4 6 0 4 4 6 0"/>',
  pendulum: '<path d="M12 3v4"/><circle cx="12" cy="7" r="1.2"/><path d="M12 7l6 12"/><circle cx="18" cy="19" r="2"/>',
  prism: '<path d="M12 4l9 16H3z"/><path d="M12 4l3 9M12 4l-3 9"/>',
  lens: '<ellipse cx="12" cy="12" rx="5" ry="9"/><path d="M4 12h2M18 12h2"/>',
  mirror: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M3 3v18M21 3v18"/>',
  telescope: '<path d="M3 16l12-8 4 4-12 8z"/><path d="M15 8l4-2 2 2-2 4"/><path d="M8 15l-3 5"/>',
  thermometer: '<path d="M12 3a2 2 0 0 0-2 2v9a4 4 0 1 0 4 0V5a2 2 0 0 0-2-2z"/><path d="M12 9v6"/>',
  circuit: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h4M13 12h6"/><rect x="10" y="9" width="4" height="6" rx="1"/>',
  resistor: '<path d="M2 12h3l1.5-4 3 8 3-8 3 8 1.5-4h4"/>',
  power_switch: '<circle cx="4" cy="18" r="1.5"/><circle cx="20" cy="18" r="1.5"/><path d="M5.5 17l13-10"/><path d="M2 21h20"/>',
  voltmeter: '<circle cx="12" cy="12" r="9"/><path d="M12 12l4-5"/><path d="M8 15h.01M16 15h.01"/>',
  motor: '<circle cx="12" cy="12" r="6"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>',
  orbit: '<circle cx="12" cy="12" r="1.6"/><ellipse cx="12" cy="12" rx="9" ry="4"/><circle cx="21" cy="12" r="1.2"/>',
  friction: '<path d="M3 18h18"/><path d="M6 18l3-9M11 18l3-9M16 18l3-9"/>',
  sound_wave: '<path d="M3 12h2M8 8v8M12 4v16M16 8v8M21 12h-2"/>',
  laser_beam: '<circle cx="4" cy="12" r="1.5"/><path d="M6 12h14" stroke-dasharray="2 2"/><path d="M20 12l-3-3M20 12l-3 3"/>',
  solar_panel: '<rect x="3" y="7" width="18" height="10" rx="1"/><path d="M3 12h18M9 7v10M15 7v10"/>',
  wind_turbine: '<path d="M12 22V10"/><circle cx="12" cy="8" r="2"/><path d="M12 8l6-4M12 8l-6-4M12 8l0 -6"/>',

  // --- chemistry ---
  test_tube: '<path d="M9 3h6"/><path d="M10 3v13a2 2 0 0 0 4 0V3"/><path d="M10 12h4"/>',
  beaker: '<path d="M8 2h8"/><path d="M9 2v6l-5 11a1.5 1.5 0 0 0 1.4 2h13.2a1.5 1.5 0 0 0 1.4-2L15 8V2"/><path d="M6 15h12"/>',
  flask: '<path d="M10 2h4"/><path d="M11 2v6l6 11a1.5 1.5 0 0 1-1.3 2H8.3a1.5 1.5 0 0 1-1.3-2l6-11z"/><path d="M8.5 15h7"/>',
  bunsen_burner: '<path d="M12 2s-3 3-3 6a3 3 0 0 0 6 0c0-3-3-6-3-6z"/><path d="M6 22v-4a6 6 0 0 1 12 0v4"/>',
  funnel_lab: '<path d="M4 4h16l-6 8v8l-4-2v-6z"/>',
  pipette: '<path d="M12 2l4 4-2 2 4 12-2 2-4-12-2 2-4-4z"/>',
  ph_scale: '<rect x="2" y="10" width="20" height="4" rx="2"/><path d="M6 10v4M12 10v4M18 10v4"/>',
  crystal: '<path d="M12 2l6 6-2 8h-8l-2-8z"/><path d="M8 8h8M10 10l2 8 2-8"/>',
  distillation: '<circle cx="6" cy="17" r="3"/><path d="M6 14V6h6l6 6h-6v5"/>',
  gas_jar: '<path d="M8 2h8v3l2 3v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8l2-3z"/>',
  bond: '<circle cx="6" cy="12" r="3"/><circle cx="18" cy="12" r="3"/><path d="M9 12h6"/>',
  ion: '<circle cx="12" cy="12" r="5"/><path d="M12 8v-2M12 18v-2M8 12H6M18 12h-2" stroke-dasharray="1 2"/>',
  acid_drop: '<path d="M12 2s6 8 6 12a6 6 0 0 1-12 0c0-4 6-12 6-12z"/><path d="M9 16h6"/>',
  reaction_arrow: '<path d="M2 12h16"/><path d="M14 7l6 5-6 5"/>',
  combustion: '<path d="M12 2v6"/><path d="M12 22a6 6 0 0 0 6-6c0-3-2-5-3-7-.5 2-1.5 3-2.5 3 0-2 0-4-2-6-.5 3-3 5-3 8a5 5 0 0 0 0 5 6 6 0 0 0 4.5 3z"/>',
  polymer: '<circle cx="4" cy="12" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="22" cy="12" r="2"/><path d="M6 12h2M12 12h2M18 12h2"/>',
  mineral: '<path d="M4 14l4-10 4 4 4-4 4 10-8 6z"/>',
  hydrogen: '<circle cx="8" cy="12" r="3"/><circle cx="18" cy="12" r="1.5"/><path d="M11 12h4"/>',

  // --- biology ---
  cell_nucleus: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5"/>',
  mitochondria: '<ellipse cx="12" cy="12" rx="8" ry="5"/><path d="M6 10c2-2 4 2 6 0s4 2 6 0"/>',
  chloroplast: '<ellipse cx="12" cy="12" rx="8" ry="5"/><path d="M6 12h12M8 9v6M12 9v6M16 9v6"/>',
  chromosome: '<path d="M8 3c0 5 8 5 8 9s-8 4-8 9"/><path d="M16 3c0 5-8 5-8 9s8 4 8 9"/>',
  virus: '<circle cx="12" cy="12" r="5"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.5 5.5l1.5 1.5M17 17l1.5 1.5M18.5 5.5L17 7M7 17l-1.5 1.5"/>',
  bacteria: '<ellipse cx="12" cy="12" rx="7" ry="4" transform="rotate(20 12 12)"/><path d="M15 8l2-2M17 15l2 2M8 16l-2 2"/>',
  microscope: '<path d="M9 21h6"/><path d="M12 21v-5"/><path d="M7 16h10"/><circle cx="12" cy="8" r="3"/><path d="M10 11l-3 5"/><path d="M12 5V3"/>',
  stethoscope: '<path d="M6 3v6a4 4 0 0 0 8 0V3"/><circle cx="18" cy="15" r="3"/><path d="M14 9v3a4 4 0 0 0 1.5 3.1"/>',
  syringe: '<path d="M3 21l4-4"/><path d="M9 15l6-6 3 3-6 6z"/><path d="M15 6l3 3M17 4l3 3"/>',
  pill: '<rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-30 12 12)"/><path d="M9 8l6 8" transform="rotate(-30 12 12)"/>',
  bandage: '<rect x="3" y="8" width="18" height="8" rx="4"/><circle cx="8" cy="12" r="1"/><circle cx="16" cy="12" r="1"/>',
  skeleton: '<circle cx="12" cy="4" r="2.5"/><path d="M12 6.5v6M8 10h8M12 12.5l-4 8M12 12.5l4 8"/>',
  kidney: '<path d="M9 4a8 8 0 0 0 0 16c3 0 3-3 5-3a4 4 0 0 0 0-8c-2 0-2-2-5-2z"/>',
  stomach_organ: '<path d="M8 4c-3 0-5 3-5 6 0 5 4 9 9 9s7-3 7-7c0-2-1-3-3-3-1 0-1 1-2 1-2 0-2-6-6-6z"/>',
  nerve_cell: '<circle cx="12" cy="12" r="2.5"/><path d="M12 9.5V4M12 14.5V20M9.5 12H4M14.5 12H20M10 10l-4-4M14 10l4-4M10 14l-4 4M14 14l4 4"/>',
  blood_cell: '<ellipse cx="12" cy="12" rx="8" ry="5"/><ellipse cx="12" cy="12" rx="3" ry="2"/>',
  root: '<path d="M12 3v6"/><path d="M12 9c-3 2-4 5-5 10M12 9c3 2 4 5 5 10M12 9c-1 3-1 6 0 10"/>',
  stem: '<path d="M12 22V6"/><path d="M12 10c3-1 4-4 4-6M12 14c-3-1-4-3-4-5"/>',
  petal: '<path d="M12 21c0-9 6-9 6-15a6 6 0 0 0-12 0c0 6 6 6 6 15z"/>',
  photosynthesis: '<circle cx="6" cy="6" r="3"/><path d="M9 8l6 6"/><path d="M12 22v-6c0-2 1-3 3-3h4"/>',
  food_chain: '<circle cx="4" cy="12" r="2"/><path d="M6 12h4"/><circle cx="12" cy="12" r="2.5"/><path d="M14.5 12h4"/><circle cx="20" cy="12" r="3"/>',
  gene: '<path d="M8 2c0 5 8 5 8 10s-8 5-8 10"/><path d="M16 2c0 5-8 5-8 10s8 5 8 10"/><path d="M8.5 6h7M8 12h8M8.5 18h7"/>',

  // --- history ---
  monument: '<path d="M12 2l3 5h-6z"/><rect x="10" y="7" width="4" height="11"/><path d="M6 22h12l-2-4H8z"/>',
  fort: '<path d="M4 21V9l3-3v3h3V6h4v3h3V6l3 3v12z"/><path d="M4 9h16"/>',
  palace: '<path d="M4 21V10l4-4 4 4 4-4 4 4v11z"/><path d="M10 21v-6h4v6"/>',
  temple: '<path d="M12 2l9 6H3z"/><path d="M5 8v13M9 8v13M15 8v13M19 8v13"/><path d="M3 21h18"/>',
  crown: '<path d="M3 18l2-9 4 4 3-6 3 6 4-4 2 9z"/><path d="M3 18h18"/>',
  sword: '<path d="M14 3l7 7-9 9-3-3-2 2-2-2 2-2-3-3z"/><path d="M5 19l-2 2"/>',
  spear: '<path d="M20 4l-16 16"/><path d="M20 4l-3 1-1 3z"/>',
  chariot: '<circle cx="7" cy="18" r="2.5"/><circle cx="17" cy="18" r="2.5"/><path d="M4 18h1M20 18h-1M7 18h10"/><path d="M9 18V9h8l3-4"/>',
  scroll: '<path d="M6 4a2 2 0 0 0 0 4h12V4"/><path d="M18 20a2 2 0 0 0 0-4H6v4"/><path d="M6 8v8"/>',
  coin_ancient: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M8 12h8"/>',
  pottery: '<path d="M9 3h6v3l2 3v9a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9l2-3z"/><path d="M7 12h10"/>',
  statue: '<circle cx="12" cy="5" r="2.5"/><path d="M12 7.5v6"/><path d="M8 13h8l1 9H7z"/><path d="M5 22h14"/>',
  pyramid: '<path d="M12 3l10 18H2z"/><path d="M7 12h10"/>',
  ship_sail: '<path d="M6 20h12l-2-9H8z"/><path d="M12 11V3"/><path d="M12 4l6 6"/><path d="M12 4l-4 5"/>',
  trade_route: '<circle cx="4" cy="18" r="1.5"/><circle cx="20" cy="6" r="1.5"/><path d="M5 17l14-10" stroke-dasharray="2 2"/>',
  printing_press: '<rect x="4" y="10" width="16" height="6" rx="1"/><path d="M8 10V6h8v4"/><path d="M7 16l-1 5h12l-1-5"/>',
  railway: '<path d="M2 20l20-16"/><path d="M6 18l3-3M10 14l3-3M14 10l3-3"/>',
  cannon: '<circle cx="7" cy="14" r="5"/><path d="M7 9v10"/><rect x="6" y="12" width="14" height="4" rx="2"/>',

  // --- geography ---
  volcano: '<path d="M3 20l7-14 2 4 2-4 7 14z"/><path d="M12 6l-1-3M12 6l1-3"/>',
  valley: '<path d="M2 20l6-14 4 8 4-8 6 14z"/>',
  desert: '<path d="M2 18c2-4 4 4 6 0s4 4 6 0 4 4 6 0"/><circle cx="19" cy="6" r="2.5"/>',
  delta: '<path d="M12 2v10"/><path d="M12 12l-6 8M12 12l6 8M12 12l-2 8M12 12l2 8"/>',
  glacier: '<path d="M2 20l4-12 3 5 3-8 3 8 3-5 4 12z"/>',
  island: '<ellipse cx="12" cy="18" rx="9" ry="2.5"/><path d="M8 18c0-6 2-9 4-13 2 4 4 7 4 13"/>',
  forest: '<path d="M6 21V9M6 3l3 6H3zM6 6l3 6H3z"/><path d="M14 21V6M14 2l4 7h-8zM14 6l4 7h-8z"/>',
  wetland: '<path d="M2 12c1.5-2 3.5-2 5 0s3.5 2 5 0 3.5-2 5 0 3.5 2 5 0"/><path d="M6 16v5M12 16v5M18 16v5"/>',
  coral_reef: '<path d="M4 21V13c0-2 2-2 2-4s-2-2-2-4"/><path d="M10 21V15c0-2 2-2 2-4s-2-2-2-4"/><path d="M16 21V17c0-2 2-2 2-4s-2-2-2-4"/>',
  cyclone: '<path d="M12 12c-4 0-8-2-8-6M12 12c4 0 8 2 8 6M12 12c2 4 0 8-4 8M12 12c-2-4 0-8 4-8"/>',
  earthquake: '<path d="M2 12h4l2-4 3 8 3-8 2 4h4"/><path d="M2 12l1 6M22 12l-1 6"/>',
  river_flow: '<path d="M4 4c4 4-2 6 2 10s-2 6 2 6"/>',
  waterfall: '<path d="M4 3h12v6l-6 12"/><path d="M8 9v4M12 9v4M16 9v4"/>',
  canyon: '<path d="M2 6v16h4V10h4v10h4V6h4v16h4V6"/>',
  cave: '<path d="M2 21c2-10 6-16 10-16s8 6 10 16z"/><ellipse cx="12" cy="21" rx="4" ry="2"/>',
  cliff: '<path d="M2 21V13l10-9v17z"/><path d="M12 21V9l10 6v6z"/>',
  dune: '<path d="M2 18c3-6 6-6 9 0 3-8 6-8 11 0"/>',
  continent: '<circle cx="12" cy="12" r="9"/><path d="M6 8c2 0 2 2 4 2s1-3 4-2 2 4 4 2M5 15c2-1 3 1 5 0s2 2 4 1 3-2 4 0"/>',

  // --- civics / economics ---
  ballot: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 12l3 3 5-6"/>',
  voting_box: '<path d="M4 9h16v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M9 9V6a3 3 0 0 1 6 0v3"/><path d="M12 9v3"/>',
  parliament: '<path d="M4 21V11l8-6 8 6v10"/><path d="M4 21h16"/><path d="M8 21v-8M12 21v-8M16 21v-8"/>',
  gavel: '<path d="M14 3l7 7-3 3-7-7z"/><path d="M11 6l-7 7 4 4 7-7"/><path d="M2 22l6-6"/>',
  court_building: '<path d="M4 21V9l8-5 8 5v12"/><path d="M2 21h20"/><circle cx="12" cy="12" r="2"/>',
  law_book: '<path d="M4 4h13a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3z"/><path d="M4 4v13"/><path d="M9 9h7M9 13h5"/>',
  rights_hand: '<path d="M12 3v6"/><path d="M6 9h12l2 5H4z"/><path d="M4 14h16v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
  ballot_result: '<rect x="5" y="11" width="14" height="9" rx="1"/><path d="M12 11V5"/><path d="M9 5h6"/><path d="M9 15h6"/>',
  tax: '<circle cx="12" cy="12" r="9"/><path d="M8 16L16 8M9 10a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM15 16a1 1 0 1 0 0-2 1 1 0 0 0 0 2z"/>',
  budget: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><circle cx="7" cy="14.5" r="1.2"/>',
  stock_chart: '<path d="M3 21V3"/><path d="M3 21h18"/><path d="M6 17l4-6 3 3 5-8"/>',
  market_stall: '<path d="M3 8l2-5h14l2 5"/><path d="M4 8h16v13H4z"/><path d="M9 21v-6h6v6"/>',
  supply_demand: '<path d="M3 20L11 4"/><path d="M21 20L13 4"/><circle cx="12" cy="12" r="1.4"/>',
  inflation_chart: '<path d="M3 21h18"/><path d="M5 21V13M10 21V9M15 21V5M20 21V11"/>',
  currency_exchange: '<circle cx="8" cy="8" r="5"/><circle cx="16" cy="16" r="5"/><path d="M8 6v4M6 8h4M16 14v4M14 16h4"/>',
  labor: '<circle cx="12" cy="5" r="2.5"/><path d="M6 21v-5a3 3 0 0 1 3-3h6a3 3 0 0 1 3 3v5"/><path d="M9 13V9a3 3 0 0 1 6 0v4"/>',

  // --- math ---
  triangle_shape: '<path d="M12 3l9 18H3z"/>',
  square_shape: '<rect x="4" y="4" width="16" height="16"/>',
  pentagon_shape: '<path d="M12 2l9 6.5-3.4 10.5H6.4L3 8.5z"/>',
  hexagon_shape: '<path d="M8 3h8l4 7-4 7H8l-4-7z"/>',
  cube_3d: '<path d="M4 7l8-4 8 4-8 4z"/><path d="M4 7v10l8 4 8-4V7"/><path d="M12 11v10"/>',
  sphere_3d: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="9" ry="3.5"/>',
  cylinder_3d: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12a7 3 0 0 0 14 0V6"/>',
  angle: '<path d="M4 20h16"/><path d="M4 20L16 4"/><path d="M9 20a7 7 0 0 1 3-11" stroke-dasharray="1 2"/>',
  parallel_lines: '<path d="M5 6h14M5 18h14"/>',
  perpendicular: '<path d="M6 3v18"/><path d="M6 12h14"/>',
  fraction: '<path d="M5 19L19 5"/><circle cx="7" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
  percent: '<circle cx="7" cy="7" r="3"/><circle cx="17" cy="17" r="3"/><path d="M19 5L5 19"/>',
  infinity: '<path d="M6 12a3 3 0 1 0 0 .01M18 12a3 3 0 1 0 0 .01M6 12c3-4 9 4 12 0"/>',
  probability_dice: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="8.5" cy="8.5" r="1.2"/><circle cx="15.5" cy="8.5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="8.5" cy="15.5" r="1.2"/><circle cx="15.5" cy="15.5" r="1.2"/>',

  // --- general / connective ---
  notebook: '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 3v18"/><path d="M12 8h4M12 12h4"/>',
  magnifier: '<circle cx="10" cy="10" r="6"/><path d="M15 15l6 6"/>',
  link_chain: '<path d="M9 15l6-6"/><path d="M7 12l-2 2a3.5 3.5 0 0 0 5 5l2-2"/><path d="M17 12l2-2a3.5 3.5 0 0 0-5-5l-2 2"/>',
  puzzle_piece: '<path d="M9 3h4v2a2 2 0 0 0 4 0V3h4v4h-2a2 2 0 0 0 0 4h2v4h-4v-2a2 2 0 0 0-4 0v2H9v-4H7a2 2 0 0 1 0-4h2z"/>',
  building_blocks: '<rect x="3" y="14" width="6" height="6"/><rect x="9" y="14" width="6" height="6"/><rect x="15" y="14" width="6" height="6"/><rect x="6" y="8" width="6" height="6"/><rect x="12" y="8" width="6" height="6"/>',
  ladder_steps: '<path d="M3 21L21 3"/><path d="M9 15h4M13 11h4M17 7h4M5 19h4"/>',
  milestone_flag: '<path d="M6 21V3"/><path d="M6 4h13l-3 4 3 4H6"/><circle cx="6" cy="21" r="1"/>',
  checklist: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8l1.5 1.5L12 7"/><path d="M8 15l1.5 1.5L12 14"/><path d="M14 8h4M14 15h4"/>',
  timer: '<circle cx="12" cy="13" r="8"/><path d="M12 13V9"/><path d="M9 3h6"/>',
  thumbs_up: '<path d="M7 11v10H4V11z"/><path d="M7 11l3-8a2 2 0 0 1 4 2v4h5a2 2 0 0 1 2 2.5l-2 6a2 2 0 0 1-2 1.5H7"/>',
  warning_triangle: '<path d="M12 3l10 18H2z"/><path d="M12 10v4"/><path d="M12 17v.01"/>',
  info_circle: '<circle cx="12" cy="12" r="9"/><path d="M12 8v.01"/><path d="M11 12h1v5h1"/>',
  unlock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8-1"/>',
  layers: '<path d="M12 2l9 5-9 5-9-5z"/><path d="M3 12l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',

  dot: '<circle cx="12" cy="12" r="4"/>'
};

// Category tags per icon name, used server-side (template_registry.py) to
// build a per-subject shortlist for the LLM prompt instead of dumping the
// full library into every request - see build_icon_guidance_text(subject).
// An icon may belong to more than one category. Icons not listed here (the
// original curated set) are treated as 'general' - always included.
var HFIconCategories = {
  physics: ['force_arrow','pulley','lever','spring','pendulum','prism','lens','mirror','telescope','thermometer','circuit','resistor','power_switch','voltmeter','motor','orbit','friction','sound_wave','laser_beam','solar_panel','wind_turbine','magnet','battery','wire','zap','atom'],
  chemistry: ['test_tube','beaker','flask','bunsen_burner','funnel_lab','pipette','ph_scale','crystal','distillation','gas_jar','bond','ion','acid_drop','reaction_arrow','combustion','polymer','mineral','hydrogen','atom','molecule','fire'],
  biology: ['cell_nucleus','mitochondria','chloroplast','chromosome','virus','bacteria','microscope','stethoscope','syringe','pill','bandage','skeleton','kidney','stomach_organ','nerve_cell','blood_cell','root','stem','petal','photosynthesis','food_chain','gene','heart','brain','lungs','eye','dna','bone','leaf','tree','flower','seed','paw','fish','bird','bug'],
  history: ['monument','fort','palace','temple','crown','sword','spear','chariot','scroll','coin_ancient','pottery','statue','pyramid','ship_sail','trade_route','printing_press','railway','cannon','shield','key','flag'],
  geography: ['volcano','valley','desert','delta','glacier','island','forest','wetland','coral_reef','cyclone','earthquake','river_flow','waterfall','canyon','cave','cliff','dune','continent','mountain','globe','map_pin','wave','ocean_wave','water_drop','cloud','rain','wind'],
  civics: ['ballot','voting_box','parliament','gavel','court_building','law_book','rights_hand','ballot_result','tax','budget','stock_chart','market_stall','supply_demand','inflation_chart','currency_exchange','labor','scale','coin','handshake','people','user','shield'],
  math: ['triangle_shape','square_shape','pentagon_shape','hexagon_shape','cube_3d','sphere_3d','cylinder_3d','angle','parallel_lines','perpendicular','fraction','percent','infinity','probability_dice','chart_bar','chart_line','ruler','target'],
  general: ['notebook','magnifier','link_chain','puzzle_piece','building_blocks','ladder_steps','milestone_flag','checklist','timer','thumbs_up','warning_triangle','info_circle','unlock','layers','lightbulb','book','pencil','gear','flag','trophy','star','check','cross','arrow_up','arrow_down','arrow_right','arrow_left','cycle','clock','calendar','home','factory','message','lock','database','filter','rocket','plane','car','boat','hourglass','wave_hand','question','bell','compass','water_drop','cloud','rain','wave','ocean_wave','wind','sun']
};

// Returns the inner SVG markup for a given icon name, falling back to a
// plain dot so a missing/unrecognized icon name never breaks rendering.
function getIconMarkup(name) {
  if (!name) return HFIcons.dot;
  var key = String(name).toLowerCase().trim().replace(/[\s-]+/g, '_');
  if (!HFIcons[key]) {
    var warn = (typeof console !== 'undefined' && console.warn) ? console.warn : function () {};
    warn('[HYPERFRAMES_ICON_FALLBACK] requested="' + name + '" normalized="' + key + '" -> falling back to dot (icon not in library)');
    return HFIcons.dot;
  }
  return HFIcons[key];
}

// Dual export: usable via require() at HTML-compile time in Node (Renderer.js
// embeds icon markup directly into the generated HTML string) and via
// <script src="./shared/icons.js"> in the browser, matching theme.js/animations.js.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HFIcons: HFIcons, getIconMarkup: getIconMarkup, HFIconCategories: HFIconCategories };
} else {
  window.HFIcons = HFIcons;
  window.getIconMarkup = getIconMarkup;
  window.HFIconCategories = HFIconCategories;
}
