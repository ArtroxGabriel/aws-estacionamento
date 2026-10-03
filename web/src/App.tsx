import { Route, Routes } from "react-router";
import Layout from "./components/Layout";
import AuditPage from "./pages/AuditPage";
import DashboardPage from "./pages/DashboardPage";
import EntryPage from "./pages/EntryPage";
import NotFoundPage from "./pages/NotFoundPage";
import PaymentPage from "./pages/PaymentPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="entrada" element={<EntryPage />} />
        <Route path="saida" element={<PaymentPage />} />
        <Route path="auditoria" element={<AuditPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
