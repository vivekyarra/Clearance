export type JoinDevice = "ios" | "android" | "other";

/** iPadOS reports a Macintosh UA; touch points tell it apart from a Mac. */
export function detectDevice(ua: string, maxTouchPoints = 0): JoinDevice {
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && maxTouchPoints > 1)) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "other";
}
