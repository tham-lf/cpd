module.exports = {
  apps: [
    {
      name: "streamlit-app",
      script: "venv/bin/streamlit",
      args: "run app.py --server.port 8501 --server.address 0.0.0.0",
      interpreter: "none",
      env: {
        NODE_ENV: "production",
      }
    },
    {
      name: "telegram-bot",
      script: "venv/bin/python",
      args: "bot.py",
      interpreter: "none",
      env: {
        NODE_ENV: "production",
      }
    },
    {
      name: "whatsapp-bot",
      script: "venv/bin/python",
      args: "whatsapp_bot.py",
      interpreter: "none",
      env: {
        NODE_ENV: "production",
        WHATSAPP_PORT: "5000",
      }
    }
  ]
};
