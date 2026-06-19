package com.insurance.gateway.rabbit;

import java.nio.charset.StandardCharsets;

import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageListener;
import org.springframework.stereotype.Component;

import com.insurance.gateway.ws.ChatWebSocketHandler;

/**
 * 이 인스턴스 전용 응답 큐의 리스너.
 * worker 가 회신한 메시지를 correlationId 로 WS 세션에 매칭해 전달한다.
 */
@Component
public class ReplyListener implements MessageListener {

    private final ChatWebSocketHandler handler;

    public ReplyListener(ChatWebSocketHandler handler) {
        this.handler = handler;
    }

    @Override
    public void onMessage(Message message) {
        String correlationId = message.getMessageProperties().getCorrelationId();
        String body = new String(message.getBody(), StandardCharsets.UTF_8);
        handler.routeReply(correlationId, body);
    }
}
