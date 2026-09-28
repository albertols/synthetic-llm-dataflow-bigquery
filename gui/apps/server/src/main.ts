/** Starts the BFF: `npm start` (mock by default; DATA_SOURCE=bigquery GCP_PROJECT=… for live data). */
import { buildApp } from "./app";
import { isLoopbackHost, loadConfig } from "./config";
import { BigQueryProvider } from "./providers/bigquery";
import { MockProvider } from "./providers/mock";

const config = loadConfig();
const provider = config.DATA_SOURCE === "bigquery" ? new BigQueryProvider(config) : new MockProvider();
const app = await buildApp({ config, provider });

for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.once(signal, () => {
    void app.close().then(() => process.exit(0));
  });

await app.listen({ host: config.HOST, port: config.PORT });
if (config.DATA_SOURCE === "bigquery" && !isLoopbackHost(config.HOST))
  app.log.warn(
    [
      "",
      "!".repeat(78),
      `!! HOST=${config.HOST} is not loopback and DATA_SOURCE=bigquery: anyone who can reach this`,
      `!! interface reads BigQuery data and spends bytes as ${provider.project ?? "the runner"}'s credentials.`,
      "!! The Host / Origin guard still applies (ALLOWED_HOSTS). Inside a container, publish only",
      "!! on loopback: docker run -p 127.0.0.1:8787:8787 …",
      "!".repeat(78),
    ].join("\n"),
  );
app.log.info(
  `Synthetic Platform BFF on http://${config.HOST}:${config.PORT} — data source ${provider.mode}` +
    (provider.project ? ` (${provider.project})` : ""),
);
// Warm the mock dataset after the socket is open, so health and the SPA answer at once.
setImmediate(() => {
  const started = Date.now();
  void provider.ready().then(() => app.log.info(`data provider ready in ${Date.now() - started} ms`));
});
