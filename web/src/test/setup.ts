import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Sem `globals: true` a Testing Library não registra o cleanup automaticamente.
afterEach(() => {
  cleanup();
});
