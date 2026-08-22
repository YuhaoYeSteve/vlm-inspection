import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";

test("renders the four primary navigation destinations", () => {
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );

  expect(screen.getByRole("link", { name: "运行任务" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "提示词模板" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "历史记录" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "设置" })).toBeInTheDocument();
});
