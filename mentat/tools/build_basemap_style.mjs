// Build the offline street style once (build packet s3, "Two renderers, one layer model").
//
//   node mentat/tools/build_basemap_style.mjs
//
// Reads the @protomaps/basemaps 5.7.2 style builder lane E downloaded into
// mentat/.cache/basemap/build and writes mentat/.cache/basemap/style/{light,dark}.json.
// Every URL in the output is on the console's own origin, because the console's
// Content-Security-Policy is default-src 'self' and the map must work offline:
//   tiles   pmtiles:///basemap/tiles/<archive>.pmtiles   (server.py answers HTTP Range)
//   glyphs  /basemap/fonts/{fontstack}/{range}.pbf        (Noto Sans Regular and Medium)
//   sprite  /basemap/sprites/v4/light                     (the one sprite set downloaded)
// map-streets.mjs turns the relative URLs into absolute ones at load time.
//
// The builder asks for three font stacks; only Regular and Medium were
// downloaded (lane E), so Italic is written as Regular rather than left to 404.
// Nothing here reaches the network. Node 20+ (ES modules).
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const mentat = path.resolve(here, '..');
const basemap = path.join(mentat, '.cache', 'basemap');
const builder = path.join(basemap, 'build', 'protomaps-basemaps-5.7.2', 'package', 'dist', 'esm', 'index.js');
const outDir = path.join(basemap, 'style');
const ARCHIVE = 'nyc-metro-z15.pmtiles';
const FONTS = ['Noto Sans Regular', 'Noto Sans Medium'];

if (!fs.existsSync(builder)) {
  console.error('The style builder is not in ' + builder + '. Lane E downloads it into mentat/.cache/basemap/build.');
  process.exit(2);
}
const {layers, namedFlavor} = await import(pathToFileURL(builder).href);

function fontsIn(value, found) {
  if (Array.isArray(value)) {
    if (value.length && value.every(item => typeof item === 'string') && value.some(item => /Noto Sans/.test(item))) {
      for (const item of value) found.add(item);
    }
    for (const item of value) fontsIn(item, found);
  } else if (value && typeof value === 'object') {
    for (const item of Object.values(value)) fontsIn(item, found);
  }
  return found;
}
function swapFonts(value) {
  if (Array.isArray(value)) return value.map(item => typeof item === 'string' && /^Noto Sans /.test(item) && !FONTS.includes(item) ? FONTS[0] : swapFonts(item));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, swapFonts(item)]));
  return value;
}

fs.mkdirSync(outDir, {recursive: true});
const written = [];
for (const flavorName of ['light', 'dark']) {
  const built = layers('protomaps', namedFlavor(flavorName), {lang: 'en'});
  const asked = [...fontsIn(built, new Set())].sort();
  const style = {
    version: 8,
    name: 'Mentat offline streets (' + flavorName + ')',
    metadata: {
      'mentat:generator': 'mentat/tools/build_basemap_style.mjs',
      'mentat:builder': '@protomaps/basemaps 5.7.2 (' + flavorName + ' flavor, lang en)',
      'mentat:archive': ARCHIVE,
      'mentat:fonts_asked': asked,
      'mentat:fonts_served': FONTS,
      'mentat:sprite': 'v4/light (the only sprite set downloaded; the dark flavor reuses it)'
    },
    sources: {
      protomaps: {
        type: 'vector',
        url: 'pmtiles:///basemap/tiles/' + ARCHIVE,
        attribution: 'MapLibre | Protomaps | © OpenStreetMap contributors'
      }
    },
    glyphs: '/basemap/fonts/{fontstack}/{range}.pbf',
    sprite: '/basemap/sprites/v4/light',
    layers: swapFonts(built)
  };
  const target = path.join(outDir, flavorName + '.json');
  fs.writeFileSync(target + '.tmp', JSON.stringify(style, null, 1) + '\n');
  fs.renameSync(target + '.tmp', target);
  written.push({flavor: flavorName, file: target, layers: style.layers.length, fonts_asked: asked});
}
console.log(JSON.stringify(written, null, 1));
