export function wifiNetworkError(ssid: string, password: string): string {
  const ssidCharacters = [...ssid];
  const ssidBytes = new TextEncoder().encode(ssid).length;
  const invalidSsidCharacter = ssidCharacters.some((character) => {
    const codePoint = character.codePointAt(0);
    return (
      codePoint === undefined ||
      codePoint < 32 ||
      codePoint === 127 ||
      (codePoint >= 0xd800 && codePoint <= 0xdfff)
    );
  });
  if (ssidBytes < 1 || ssidBytes > 32 || invalidSsidCharacter)
    return "SSID must contain 1 to 32 UTF-8 bytes without controls.";
  const rawPsk = /^[0-9A-Fa-f]{64}$/.test(password);
  const printableAscii = [...password].every((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && codePoint >= 32 && codePoint <= 126;
  });
  if (
    !rawPsk &&
    !(password.length >= 8 && password.length <= 63 && printableAscii)
  )
    return "Password must be 8 to 63 printable ASCII characters or 64 hexadecimal digits.";
  return "";
}
