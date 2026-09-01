// 用 SnowLuma 自带的 ffmpegAddon 把 SILK/AMR 转成 wav/mp3
// 用法: node decode_voice.cjs <输入文件> <输出文件> <格式:wav|mp3>
'use strict';
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const [,, inPath, outPath, format = 'wav'] = process.argv;

function addonFileName() {
  return `ffmpegAddon.${process.platform}.${process.arch}.node`;
}

function resolveAddonPath() {
  const dirs = [
    path.resolve(__dirname, 'native', 'ffmpeg'),
    path.resolve(process.cwd(), 'native', 'ffmpeg'),
  ];
  for (const dir of dirs) {
    const full = path.join(dir, addonFileName());
    if (fs.existsSync(full)) return full;
  }
  return null;
}

(async () => {
  try {
    const addonPath = resolveAddonPath();
    if (!addonPath) throw new Error('ffmpegAddon not found');
    // 加载原生插件
    const mod = { exports: {} };
    process.dlopen(mod, addonPath);
    const addon = mod.exports;
    if (typeof addon.decodeAudioToFmt !== 'function') {
      throw new Error('addon.decodeAudioToFmt is not a function');
    }
    await addon.decodeAudioToFmt(inPath, outPath, format);
    if (!fs.existsSync(outPath)) throw new Error('no output file');
    console.log('OK ' + outPath + ' ' + fs.statSync(outPath).size);
  } catch (e) {
    console.error('ERR ' + (e && e.message ? e.message : String(e)));
    process.exit(1);
  }
})();
