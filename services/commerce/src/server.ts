import express, { type Request, type Response, type NextFunction } from "express";
import cors from "cors";
import { z } from "zod";
import { config } from "./config.js";
import { listProducts } from "./catalog.js";
import {
  OrderLifecycleConflictError,
  OrderNotFoundError,
  createPaidOrder,
  fulfillOrder,
  getOrderStatus,
  listOrdersByWallet,
  markOrderRefunded,
  trackOrder,
} from "./shopify.js";

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

const ProductQuerySchema = z.object({
  query: z.string().trim().max(200).default(""),
  limit: z.coerce.number().int().min(1).max(50).default(20),
});

app.get(
  "/products",
  asyncH(async (req, res) => {
    const input = ProductQuerySchema.parse(req.query);
    res.json({ products: await listProducts(input.query, input.limit) });
  }),
);

const OrderSchema = z.object({
  orderRef: z.string(),
  productId: z.string(),
  variantId: z.string().optional(),
  sku: z.string().optional(),
  title: z.string(),
  amount: z.string(),
  buyerAddress: z.string(),
  shipTo: z.string(),
  paymentReference: z.string().optional(),
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

const WalletOrdersQuerySchema = z.object({
  buyerAddress: z.string().trim().min(32).max(64),
});

app.get(
  "/orders",
  asyncH(async (req, res) => {
    const input = WalletOrdersQuerySchema.parse(req.query);
    res.json({ orders: await listOrdersByWallet(input.buyerAddress) });
  }),
);

app.get(
  "/orders/:identifier",
  asyncH(async (req, res) => {
    res.json(await getOrderStatus(req.params.identifier!));
  }),
);

const RefundOrderSchema = z.object({
  refundReference: z.string().min(1),
  refundTxSignature: z.string().min(1),
  refundExplorer: z.string().url(),
});
app.post(
  "/orders/:orderRef/refund",
  asyncH(async (req, res) => {
    const input = RefundOrderSchema.parse(req.body);
    res.json(
      await markOrderRefunded(
        req.params.orderRef!,
        input.refundReference,
        input.refundTxSignature,
        input.refundExplorer,
      ),
    );
  }),
);

app.post(
  "/orders/:orderRef/fulfill",
  asyncH(async (req, res) => {
    res.json(await fulfillOrder(req.params.orderRef!));
  }),
);

app.get(
  "/orders/:identifier/tracking",
  asyncH(async (req, res) => {
    res.json(await trackOrder(req.params.identifier!));
  }),
);

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const message = err instanceof Error ? err.message : String(err);
  const status =
    err instanceof z.ZodError
      ? 400
      : err instanceof OrderNotFoundError
        ? 404
        : err instanceof OrderLifecycleConflictError
          ? 409
          : 500;
  console.error("[commerce] error:", message);
  res.status(status).json({ error: message });
});

app.listen(config.port, () => {
  console.log(`[commerce] listening on :${config.port} (mock=${config.mock})`);
});
