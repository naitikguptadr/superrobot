import i18n from "i18next";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import { App } from "./App";
import "@datarobot/design-system/styles/index.min.css";

i18n.init({
  lng: "en",
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nextProvider i18n={i18n}>
      <App />
    </I18nextProvider>
  </StrictMode>,
);
