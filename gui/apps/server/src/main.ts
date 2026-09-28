/** Starts the BFF: `npm start` (mock by default; DATA_SOURCE=bigquery GCP_PROJECT=… for live data). */
import { buildApp } from "./app";
import { loadConfig } from "./config";
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
app.log.info(
  `Synthetic Platform BFF on http://${config.HOST}:${config.PORT} — data source ${provider.mode}` +
    (provider.project ? ` (${provider.project})` : ""),
);
// Warm the mock dataset after the socket is open, so health and the SPA answer at once.
setImmediate(() => {
  const started = Date.now();
  void provider.ready().then(() => app.log.info(`data provider ready in ${Date.now() - started} ms`));
});
