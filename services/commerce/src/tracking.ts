import type { TrackingInfo } from "@arb/shared";
import { config } from "./config.js";

export const DEMO_CARRIER = "USPS";
export const DEMO_TRACKING_NUMBER = "EZ2000000002";
export const DEMO_TRACKING_MESSAGE =
  "DEMO tracking value only — Relay did not create or ship a real parcel.";

export interface CarrierTrackingProvider {
  lookup(trackingNumber: string, carrier: string): Promise<TrackingInfo>;
}

interface EasyPostTracker {
  tracking_code: string;
  carrier: string;
  status: string;
  status_detail: string | null;
  public_url: string | null;
  est_delivery_date: string | null;
}

/** Official EasyPost Tracker API adapter; replaceable when #36 supplies waybills. */
export class EasyPostTrackingProvider implements CarrierTrackingProvider {
  constructor(
    private readonly apiKey: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async lookup(trackingNumber: string, carrier: string): Promise<TrackingInfo> {
    const auth = Buffer.from(`${this.apiKey}:`).toString("base64");
    const response = await this.fetchImpl("https://api.easypost.com/v2/trackers", {
      method: "POST",
      headers: {
        Authorization: `Basic ${auth}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        tracker: {
          tracking_code: trackingNumber,
          carrier,
        },
      }),
    });
    if (!response.ok) {
      throw new Error(
        `EasyPost tracker lookup failed (${response.status}): ${await response.text()}`,
      );
    }
    const tracker = (await response.json()) as EasyPostTracker;
    return {
      provider: "easypost",
      carrier: tracker.carrier || carrier,
      trackingNumber: tracker.tracking_code || trackingNumber,
      status: tracker.status || "unknown",
      statusDetail: tracker.status_detail,
      trackingUrl: tracker.public_url,
      estimatedDeliveryAt: tracker.est_delivery_date,
      demo: true,
      message: DEMO_TRACKING_MESSAGE,
    };
  }
}

export function demoTrackingInfo(
  trackingNumber = DEMO_TRACKING_NUMBER,
  carrier = DEMO_CARRIER,
): TrackingInfo {
  return {
    provider: "easypost",
    carrier,
    trackingNumber,
    status: "demo",
    statusDetail: "official_api_not_configured",
    trackingUrl: null,
    estimatedDeliveryAt: null,
    demo: true,
    message: DEMO_TRACKING_MESSAGE,
  };
}

export async function lookupShipmentTracking(
  trackingNumber: string,
  carrier: string,
): Promise<TrackingInfo> {
  if (!config.tracking.easypostApiKey) {
    console.warn(
      `[tracking] DEMO number ${trackingNumber}; EASYPOST_API_KEY is not configured, ` +
        "so no real carrier lookup was performed",
    );
    return demoTrackingInfo(trackingNumber, carrier);
  }
  console.log(
    `[tracking] querying official EasyPost API for DEMO number ${trackingNumber}; ` +
      "this does not represent a real shipment",
  );
  return new EasyPostTrackingProvider(config.tracking.easypostApiKey).lookup(
    trackingNumber,
    carrier,
  );
}
