const EXT_BY_MIME: Record<string, string> = {
  "image/svg+xml": "svg",
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
};

export function extFromMime(mime: string | null | undefined): string {
  if (!mime) return "bin";
  return EXT_BY_MIME[mime] ?? "bin";
}
