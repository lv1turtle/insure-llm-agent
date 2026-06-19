package com.insurance.gateway.ws;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import org.springframework.amqp.core.AnonymousQueue;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.insurance.gateway.config.RabbitConfig;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;

/**
 * 브라우저 WebSocket 과 RabbitMQ 사이의 중계.
 * - 수신한 메시지를 correlationId 와 함께 요청 큐로 publish (replyTo = 이 인스턴스 전용 큐)
 * - worker 응답이 오면 correlationId 로 원래 WS 세션을 찾아 푸시
 *
 * WS 연결은 한 인스턴스에 고정되므로 correlationId→session 맵은 인스턴스 로컬로 충분하다.
 */
@Component
public class ChatWebSocketHandler extends TextWebSocketHandler {

    private final RabbitTemplate rabbit;
    private final String replyQueueName;
    private final Map<String, WebSocketSession> pending = new ConcurrentHashMap<>();
    // 현재 활성 WebSocket 세션 수 → Micrometer 게이지로 노출
    private final AtomicInteger activeSessions = new AtomicInteger();

    public ChatWebSocketHandler(RabbitTemplate rabbit, AnonymousQueue replyQueue, MeterRegistry registry) {
        this.rabbit = rabbit;
        this.replyQueueName = replyQueue.getName();
        Gauge.builder("websocket.sessions.active", activeSessions, AtomicInteger::get)
                .description("현재 활성 WebSocket 세션 수")
                .register(registry);
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        activeSessions.incrementAndGet();
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        // 브라우저 JSON({type, session_id, message})을 그대로 worker 로 전달
        byte[] payload = message.getPayload().getBytes(StandardCharsets.UTF_8);
        String correlationId = UUID.randomUUID().toString();
        pending.put(correlationId, session);

        MessageProperties props = new MessageProperties();
        props.setCorrelationId(correlationId);
        props.setReplyTo(replyQueueName);
        props.setContentType("application/json");
        Message out = MessageBuilder.withBody(payload).andProperties(props).build();
        rabbit.send("", RabbitConfig.REQUEST_QUEUE, out);
    }

    /** worker 응답을 correlationId 로 매칭해 해당 WS 로 푸시한다. */
    public void routeReply(String correlationId, String body) {
        if (correlationId == null) {
            return;
        }
        WebSocketSession session = pending.remove(correlationId);
        if (session == null || !session.isOpen()) {
            return;
        }
        try {
            synchronized (session) {
                session.sendMessage(new TextMessage(body));
            }
        } catch (Exception ignored) {
            // 전송 실패(이미 닫힌 세션 등)는 무시
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        activeSessions.decrementAndGet();
        // 닫힌 세션을 가리키는 대기 항목 정리
        pending.values().removeIf(s -> s.getId().equals(session.getId()));
    }
}
