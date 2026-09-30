// Tiny Express server that serves the web client and proxies the API.
const express = require("express");
const { debounce } = require("./src/debounce");

const app = express();
const PORT = process.env.PORT || 3000;

app.get("/config.json", (req, res) => {
  res.json({ apiUrl: process.env.API_URL || "http://localhost:8000" });
});

const admin = express.Router();
admin.get("/stats", showStats);
admin.delete("/cache", clearCache);
app.use("/admin", admin);

app.listen(PORT, debounce(() => console.log(`listening on ${PORT}`), 0));
