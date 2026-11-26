'use client';

import { useEffect } from 'react';

export function ReloadHome() {
  useEffect(() => {
    window.location.replace('/');
  }, []);
  return null;
}
