import express, { type Request, type Response, type NextFunction } from "express";
import cors from "cors";
import { z } from "zod";
import { config } from "./config.js";
import { createPaidOrder } from "./shopify.js";

const app = express();
app.use(cors());
app.use(express.json());

const asyncH =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction) =>
    fn(req, res).catch(next);

app.get("/health", (_req, res) =>
  res.json({ ok: true, service: "commerce", mock: config.mock }),
);

const OrderSchema = z.object({
  orderRef: z.string(),
  productId: z.string(),
  title: z.string(),
  amount: z.string(),
  buyerAddress: z.string(),
  shipTo: z.string(),
  txSignature: z.string(),
  explorer: z.string(),
});

app.post(
  "/orders",
  asyncH(async (req, res) => {
    const input = OrderSchema.parse(req.body);
    res.json(await createPaidOrder(input));
  }),
);

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const message = err instanceof Error ? err.message : String(err);
  const status = err instanceof z.ZodError ? 400 : 500;
  console.error("[commerce] error:", message);
  res.status(status).json({ error: message });
});

app.listen(config.port, () => {
  console.log(`[commerce] listening on :${config.port} (mock=${config.mock})`);
});
