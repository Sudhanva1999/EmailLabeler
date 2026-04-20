module.exports = {
  apps: [
    {
      name: "emailsorter-daily",
      script: "daily_run.py",
      interpreter: ".venv/bin/python",

      // Fire at 19:00 server time every day.
      // Adjust timezone on your server with TZ env var if needed.
      cron_restart: "0 19 * * *",

      // Don't restart on normal exit — only on the cron trigger.
      autorestart: false,
      watch: false,

      out_file: "logs/daily_out.log",
      error_file: "logs/daily_err.log",
      merge_logs: true,
      time: true,         // prefix every log line with a timestamp

      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
