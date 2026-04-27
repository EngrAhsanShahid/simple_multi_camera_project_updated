# Architecture Diagram

This document outlines the architecture for the Multi-Camera Computer Vision project. 

## System Architecture

The following diagram illustrates the flow of data from camera sources through the processing pipelines, event evaluation, and out to the frontend via our API and LiveKit services.

```mermaid
graph TD
    %% External Interfaces & Consumers
    subgraph Clients["Clients / UI"]
        WebUI[Web Frontend]
        MobileApp[Mobile / External Apps]
    end

    %% Ingestion Layer
    subgraph Ingestion["Video Ingestion & Sampling"]
        Cameras[Camera Sources - RTSP/MP4]
        SourceReader[Source Reader]
        FrameSampler[Frame Sampler]
    end

    %% Core Messaging / Memory
    subgraph MessageBus["Message Bus & Buffer (Redis)"]
        RedisStream[(Redis Streams / PubSub)]
        SharedBuffer[(Shared Frame Buffer)]
    end

    %% AI Pipeline Layer
    subgraph Pipelines["Computer Vision Pipelines"]
        PipeManager[Pipeline Manager]
        DummyPipe[Dummy Pipeline]
        PeopleCountPipe[People Counter]
        PPEPipe[PPE Detection]
        WeaponPipe[Weapon Detection]
        KnifePipe[Knife Detection]
        SpikingPipe[Spiking Detection]
    end

    %% Event Processing & Alerts
    subgraph EventProcessing["Event Processing & Aggregation"]
        Aggregator[Aggregator]
        RuleEngine[Rule Engine]
        AlertEngine[Alert Engine]
    end

    %% Media & Streaming Layer
    subgraph Media["Media & Streaming Service"]
        MediaService[Media Service]
        LiveKitPublisher[LiveKit Publisher]
        LiveKitServer[(LiveKit Server)]
    end

    %% API & Core Backend Services
    subgraph APIServices["API Services"]
        API[API Service]
        Auth[Auth / JWT]
        Metrics[Metrics Collector]
    end

    %% Persistent Storage
    subgraph Storage["Persistent Storage"]
        MongoDB[(MongoDB - Configs/Alerts/Logs)]
        Minio[(MinIO - Frames/Media storage)]
    end

    %% Flow of data
    Cameras -->|Video Stream| SourceReader
    SourceReader -->|Raw Frames| FrameSampler
    FrameSampler -->|Sampled Frames| RedisStream
    FrameSampler -->|Frame Caching| SharedBuffer

    RedisStream -->|Frame Packets| PipeManager
    PipeManager -->|Distribute| DummyPipe & PeopleCountPipe & PPEPipe & WeaponPipe & KnifePipe & SpikingPipe
    
    DummyPipe & PeopleCountPipe & PPEPipe & WeaponPipe & KnifePipe & SpikingPipe -->|Pipeline Results| RedisStream
    
    RedisStream -->|Subscribe Results| Aggregator
    Aggregator -->|Aggregated AI Results| RuleEngine
    RuleEngine -->|Match against Tenant Rules| AlertEngine
    
    AlertEngine -->|Save Alert Metadata| MongoDB
    AlertEngine -->|Save Alert Image/Clip| Minio
    AlertEngine -->|Push Notifications| API

    SourceReader -->|Live Stream| MediaService
    MediaService -->|Publish WebRTC| LiveKitPublisher
    LiveKitPublisher --> LiveKitServer
    
    LiveKitServer <-->|Low Latency Stream| WebUI

    WebUI <-->|REST / WS| API
    MobileApp <-->|REST / WS| API
    
    API -->|Authenticate| Auth
    API -->|Fetch Config/Alerts| MongoDB
    API <-->|Live Alert Stream| RedisStream

    %% Additional Storage Connections
    RedisStream -.-> Metrics
```

## Component Description

### 1. Ingestion Layer
*   **Source Reader**: Connects to the configured cameras (RTSP, video files, etc.) and captures continuous streams.
*   **Frame Sampler**: Samples frames at designated FPS to reduce load, pushing these normalized frames onto the shared message queue (Redis) and storing them in an ephemeral `Shared Buffer`.

### 2. Message Bus
*   **Redis Streams/PubSub**: Acts as the central nervous system of the architecture, decoupling the ingestion, processing, and alerting components. Facilitated by the `redis_stream_sdk`.

### 3. Computer Vision Pipelines
*   **Pipeline Manager**: Orchestrates multiple CV pipelines.
*   **Specialized Pipelines**: Independent modules (e.g., YOLO variants) checking frames for specific scenarios (PPE, Weapons, Counting). Output from these models is serialized as `PipelineResult` and pushed back to the message queue.

### 4. Event Processing
*   **Aggregator**: Listens to outputs from multiple pipelines for the same frame/time window and aggregates them. 
*   **Rule Engine**: Loads per-tenant or per-camera rules to evaluate if the aggregated objects meet conditions for an alert.
*   **Alert Engine**: Processes triggered alerts, persisting them and storing referencing frames in remote object storage.

### 5. API & Services
*   **API Service**: A RESTful / WebSocket service (likely built on FastAPI) that handles frontend requests, user authentication, and streams alerts in real-time.
*   **LiveKit Service**: Allows clients to view low-latency WebRTC streams directly from the source.

### 6. Storage
*   **MinIO**: Object storage designed for saving captured rule-violating frames or incident video clips securely.
*   **MongoDB**: Primary NoSQL datastore managing configurations (cameras, tenants, rules) and logs/alerts.
