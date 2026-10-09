// Local testing: on localhost the app auto-connects to the Firebase emulators
// with a demo project (no real Google project needed).
// For production: replace with the real web app config from Firebase console.
if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
  window.FIREBASE_CONFIG = {
    apiKey: "demo-key",
    authDomain: "demo-awetisticats.firebaseapp.com",
    projectId: "demo-awetisticats",
  };
  window.USE_EMULATORS = true;
} else {
  window.FIREBASE_CONFIG = {
    apiKey: "REPLACE_ME",
    authDomain: "REPLACE_ME.firebaseapp.com",
    projectId: "REPLACE_ME",
  };
}
