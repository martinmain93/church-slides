/**
 * Browser-native file download / pick helpers.
 *
 * We deliberately avoid `@tauri-apps/plugin-dialog` here so the same code
 * path works in both the Tauri shell and the browser dev preview. Both
 * environments support a hidden `<input type="file">` and Blob-URL
 * downloads via a temporary `<a>` click.
 */

export function downloadText(filename: string, content: string, mime = "application/json"): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // Defer revocation so the browser has a chance to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function pickTextFile(accept = "application/json"): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.style.display = "none";
    let resolved = false;

    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) {
        resolved = true;
        resolve(null);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        resolved = true;
        resolve(typeof reader.result === "string" ? reader.result : null);
      };
      reader.onerror = () => {
        resolved = true;
        resolve(null);
      };
      reader.readAsText(file);
    });

    // Catch cancel events: not all browsers emit `cancel`, so fall back
    // to a window focus listener.
    const onWindowFocus = () => {
      window.removeEventListener("focus", onWindowFocus);
      setTimeout(() => {
        if (!resolved) resolve(null);
      }, 500);
    };
    window.addEventListener("focus", onWindowFocus);

    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
  });
}

/**
 * Pick one or more files (any content). Resolves to [] on cancel. Same
 * hidden-input approach as `pickTextFile`, so it works in the browser preview.
 */
export function pickFiles(accept: string, multiple = true, directory = false): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    // A whole folder, subfolders included (every browser engine supports it
    // under this name).
    if (directory) input.setAttribute("webkitdirectory", "");
    input.style.display = "none";
    let resolved = false;
    input.addEventListener("change", () => {
      resolved = true;
      resolve(Array.from(input.files ?? []));
    });
    const onWindowFocus = () => {
      window.removeEventListener("focus", onWindowFocus);
      setTimeout(() => {
        if (!resolved) resolve([]);
      }, 500);
    };
    window.addEventListener("focus", onWindowFocus);
    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
  });
}

export function timestampedFilename(prefix: string, ext: string): string {
  const d = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(
    d.getHours(),
  )}${pad(d.getMinutes())}`;
  return `${prefix}-${stamp}.${ext}`;
}
