import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import VAPIDashboard from "../VAPIDashboard.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <VAPIDashboard />
  </StrictMode>
);
