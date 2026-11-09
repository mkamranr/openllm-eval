import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS } from "../components/layout/Layout";
import { App } from "./App";
import { ROUTER_FUTURE } from "./routerFuture";

describe("App shell", () => {
  it("renders every main navigation item from the spec", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {}))); // pages stay loading
    render(
      <MemoryRouter future={ROUTER_FUTURE}>
        <App />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const item of NAV_ITEMS) {
      expect(nav).toHaveTextContent(item.label);
    }
    expect(NAV_ITEMS).toHaveLength(10);
  });

  it("routes to the endpoints page", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/endpoints"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Models / Endpoints" })).toBeInTheDocument();
  });

  it("routes unfinished features to placeholders", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/compare"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Compare Runs" })).toBeInTheDocument();
    expect(screen.getByText(/Milestone 6/)).toBeInTheDocument();
  });

  it("routes /runs to the runs list", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/runs"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Benchmark Runs" })).toBeInTheDocument();
  });
});
