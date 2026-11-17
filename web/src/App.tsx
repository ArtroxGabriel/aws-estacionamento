import { Navigate, Route, Routes } from "react-router";
import Layout from "./components/Layout";
import AuditPage from "./pages/AuditPage";
import DashboardPage from "./pages/DashboardPage";
import EntryPage from "./pages/EntryPage";
import ExitBarrierPage from "./pages/ExitBarrierPage";
import NotFoundPage from "./pages/NotFoundPage";
import PaymentPage from "./pages/PaymentPage";

// Uma tela por etapa da permanência: cancela de entrada, caixa, cancela de saída.
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="cancela-entrada" element={<EntryPage />} />
        <Route path="pagamento" element={<PaymentPage />} />
        <Route path="cancela-saida" element={<ExitBarrierPage />} />
        <Route path="auditoria" element={<AuditPage />} />
        {/* Endereços antigos continuam funcionando. */}
        <Route path="entrada" element={<Navigate to="/cancela-entrada" replace />} />
        <Route path="saida" element={<Navigate to="/pagamento" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
