package com.insurance.gateway.config;

import java.util.Map;

import org.springframework.amqp.core.AnonymousQueue;
import org.springframework.amqp.core.MessageListener;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RabbitConfig {

    public static final String REQUEST_QUEUE = "agent.requests";
    public static final String DLX = "agent.dlx";

    /**
     * worker 와 인자가 일치해야 한다(durable + x-dead-letter-exchange).
     * 불일치 시 RabbitMQ 가 PRECONDITION_FAILED 로 거부한다.
     */
    @Bean
    Queue requestQueue() {
        return new Queue(REQUEST_QUEUE, true, false, false,
                Map.of("x-dead-letter-exchange", DLX));
    }

    /**
     * 이 인스턴스 전용 응답 큐. 브로커가 고유 이름을 생성하며 exclusive + auto-delete 이다.
     * 응답이 "요청을 보낸 이 인스턴스"에만 도착하도록 보장하는 핵심.
     */
    @Bean
    AnonymousQueue replyQueue() {
        return new AnonymousQueue();
    }

    @Bean
    SimpleMessageListenerContainer replyContainer(ConnectionFactory connectionFactory,
                                                  AnonymousQueue replyQueue,
                                                  MessageListener replyListener) {
        SimpleMessageListenerContainer container = new SimpleMessageListenerContainer(connectionFactory);
        container.setQueues(replyQueue);
        container.setMessageListener(replyListener);
        return container;
    }
}
