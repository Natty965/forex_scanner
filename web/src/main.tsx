import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./styles/tokens.css";
import "./styles/app.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
import { DataHealth } from "./pages/DataHealth";
import { Markets } from "./pages/Markets";
import { Overview } from "./pages/Overview";
import { PAGES, Placeholder } from "./pages/Placeholder";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="markets" element={<Markets />} />
            <Route path="markets/:pid" element={<Markets />} />
            <Route path="signals" element={<Placeholder {...PAGES.signals} locked />} />
            <Route path="journal" element={<Placeholder {...PAGES.journal} locked />} />
            <Route path="backtests" element={<Placeholder {...PAGES.backtests} locked />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="data" element={<DataHealth />} />
            <Route path="*" element={<Placeholder title="Not found" text="There is no page at this address." />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
