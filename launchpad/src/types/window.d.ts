// Scout configuration attached to window object
declare global {
  interface Window {
    scoutConfig?: {
      enableChat?: boolean;
      showPlaybooks?: boolean;
    };
  }
}

export {};
