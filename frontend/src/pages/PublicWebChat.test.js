import { mergePublicMessages } from "../lib/publicWebChatMessages";

const history = {
  id: "msg_old", sender_type: "bot", body: "Hola",
  created_at: "2026-07-31T12:00:00Z", delivery_status: "sent",
};
const optimistic = {
  id: "temp_browser_1", client_message_id: "browser_1",
  sender_type: "contact", body: "Quiero un turno",
  created_at: "2026-07-31T12:00:01Z", delivery_status: "delivered",
};

test("una consulta atrasada no hace desaparecer el mensaje optimista", () => {
  const result = mergePublicMessages([history, optimistic], [history]);
  expect(result.map((item) => item.id)).toEqual(["msg_old", "temp_browser_1"]);
});

test("la confirmación del servidor reemplaza el mensaje optimista sin duplicarlo", () => {
  const confirmed = {
    ...optimistic,
    id: "msg_confirmed_1",
    delivery_status: "delivered",
  };
  const result = mergePublicMessages([history, optimistic], [history, confirmed]);
  expect(result.map((item) => item.id)).toEqual(["msg_old", "msg_confirmed_1"]);
  expect(result.filter((item) => item.client_message_id === "browser_1")).toHaveLength(1);
});

test("un mensaje fallido permanece visible hasta su reintento", () => {
  const failed = { ...optimistic, delivery_status: "failed" };
  const result = mergePublicMessages([history, failed], [history]);
  expect(result.find((item) => item.id === failed.id)?.delivery_status).toBe("failed");
});
