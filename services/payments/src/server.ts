import express, { type Request, type Response, type NextFunction } from "express";
import cors from "cors";
import { z } from "zod";
import { config } from "./config.js";
import { buyer, merchant, usdcMint } from "./solana.js";
import { createPaymentRequest, pay, verify } from "./payments.js";

const app = express();
app.use(cors());
app.use(express.json());

const asyncH =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction) =>
    fn(req, res).catch(next);

app.get("/health", (_req, res) => res.json({ ok: true, service: "payments" }));

app.get("/wallets", (_req, res) =>
  res.json({
    merchant: merchant.publicKey.toBase58(),
    buyer: buyer.publicKey.toBase58(),
    usdcMint: usdcMint.toBase58(),
    cluster: config.cluster,
  }),
);

const CreateSchema = z.object({
  productId: z.string(),
  title: z.string(),
  amount: z.string().regex(/^\d+(\.\d{1,6})?$/),
  orderRef: z.string(),
  payTo: z.string().optional(),
});
app.post(
  "/payment-requests",
  asyncH(async (req, res) => {
    const input = CreateSchema.parse(req.body);
    res.json(createPaymentRequest(input));
  }),
);

const PaySchema = z.object({
  payTo: z.string(),
  amount: z.string().regex(/^\d+(\.\d{1,6})?$/),
  reference: z.string(),
});
app.post(
  "/pay",
  asyncH(async (req, res) => {
    const input = PaySchema.parse(req.body);
    res.json(await pay(input));
  }),
);

const VerifySchema = z.object({ reference: z.string() });
app.post(
  "/verify",
  asyncH(async (req, res) => {
    const { reference } = VerifySchema.parse(req.body);
    res.json(await verify(reference));
  }),
);

// Central error handler.
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const message = err instanceof Error ? err.message : String(err);
  const status = err instanceof z.ZodError ? 400 : 500;
  console.error("[payments] error:", message);
  res.status(status).json({ error: message });
});

app.listen(config.port, () => {
  console.log(`[payments] listening on :${config.port} (cluster=${config.cluster})`);
  console.log(`[payments] merchant=${merchant.publicKey.toBase58()}`);
  console.log(`[payments] buyer=${buyer.publicKey.toBase58()}`);
});
