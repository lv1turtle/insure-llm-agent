package com.insurance.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * WAS(Tomcat) + WebSocket 게이트웨이.
 * 브라우저 WS 메시지를 RabbitMQ 요청 큐로 보내고, 응답을 correlationId 로 매칭해 WS 로 푸시한다.
 * agent/DB 로직은 알지 못하는 얇은 중계 계층이다.
 */
@SpringBootApplication
public class GatewayApplication {
    public static void main(String[] args) {
        SpringApplication.run(GatewayApplication.class, args);
    }
}
