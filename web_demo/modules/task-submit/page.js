(function () {
  window.TaskSubmitPage = {
    route: "task-submit", title: "步骤工作台",
    render: ctx => window.TaskReportPage.render(ctx),
    mount: ctx => window.TaskReportPage.mount(ctx)
  };
})();
