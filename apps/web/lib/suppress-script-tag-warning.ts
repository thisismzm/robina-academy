// React 19 (shipped with Next.js 16) logs a console error for ANY <script> tag
// rendered inside a React component tree:
//
//   "Encountered a script tag while rendering React component. Scripts inside
//    React components are never executed when rendering on the client..."
//
// For us this is a false positive. The three synchronous scripts in
// app/layout.tsx's <head> (dir-init.js, runtime-config.js, embed-bg.js) are
// emitted in the initial server-rendered HTML and execute there, synchronously,
// before body paint — which is exactly what they are built for. They are never
// meant to re-execute during client hydration, so React's warning does not
// describe a real problem here.
//
// We deliberately keep plain <script src> tags instead of next/script
// (strategy="beforeInteractive") because our scripts must block parsing and run
// in a fixed order before the body paints, and beforeInteractive is not truly
// blocking in all browsers (notably Safari). See the comments in layout.tsx.
//
// So we filter just this one message, and only in development, where the Next.js
// dev overlay surfaces it on every page load. Production console output is
// untouched.
//
// Importing this module for its side effect runs the patch once on the client.
if (
  typeof window !== 'undefined' &&
  process.env.NODE_ENV === 'development'
) {
  // eslint-disable-next-line no-console -- intentionally wrapping console.error
  const original = console.error
  // eslint-disable-next-line no-console -- intentionally overriding console.error
  console.error = (...args: unknown[]) => {
    if (
      typeof args[0] === 'string' &&
      args[0].includes('Encountered a script tag')
    ) {
      return
    }
    original.apply(console, args)
  }
}

export {}
