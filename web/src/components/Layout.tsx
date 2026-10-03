import { Link, NavLink, Outlet } from "react-router";

const links = [
  { to: "/", label: "Painel", end: true },
  { to: "/entrada", label: "Entrada", end: false },
  { to: "/saida", label: "Saída", end: false },
  { to: "/auditoria", label: "Auditoria", end: false },
];

function navClass({ isActive }: { isActive: boolean }) {
  const base = "rounded-md px-3 py-2 text-sm font-medium";
  return isActive
    ? `${base} bg-blue-600 text-white`
    : `${base} text-slate-300 hover:bg-slate-800 hover:text-white`;
}

export default function Layout() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="bg-slate-900">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <Link to="/" className="text-lg font-semibold text-white">
            Estacionamento
          </Link>
          <nav aria-label="Navegação principal" className="flex flex-wrap gap-1">
            {links.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.end} className={navClass}>
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
