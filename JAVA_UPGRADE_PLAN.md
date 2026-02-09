# Java 1.7 → 21 마이그레이션 계획서 (OpenRewrite 기반)

> **환경**: 금융권 폐쇄망 (Air-Gapped Network)
> **도구**: OpenRewrite
> **대상**: Java 1.7 → Java 21 업그레이드
> **작성일**: 2026-02-09

---

## 목차

1. [개요](#1-개요)
2. [마이그레이션 전략](#2-마이그레이션-전략)
3. [eGovFramework 호환성 분석](#3-egovframework-호환성-분석)
4. [Phase 1 — 인터넷 환경 준비 (오프라인 패키지 구성)](#4-phase-1--인터넷-환경-준비-오프라인-패키지-구성)
5. [Phase 2 — 폐쇄망 환경 설정](#5-phase-2--폐쇄망-환경-설정)
6. [Phase 3 — 단계별 마이그레이션 실행](#6-phase-3--단계별-마이그레이션-실행)
7. [Phase 4 — 검증 및 테스트](#7-phase-4--검증-및-테스트)
8. [pom.xml 기반 의존성 영향도 분석 방법](#8-pomxml-기반-의존성-영향도-분석-방법)
9. [롤백 계획](#9-롤백-계획)
10. [금융권 특수 고려사항](#10-금융권-특수-고려사항)
11. [체크리스트 (전체)](#11-체크리스트-전체)
12. [참고 자료](#12-참고-자료)

---

## 1. 개요

### 1.1 왜 Java 21인가?

| 항목 | Java 7 (EOL) | Java 21 (LTS) |
|------|--------------|----------------|
| 보안 패치 | 중단됨 | 2031년까지 지원 |
| 성능 | 기본 G1 GC 없음 | ZGC, Virtual Threads |
| 언어 기능 | 제한적 | Record, Sealed Class, Pattern Matching |
| 생태계 | 최신 라이브러리 미지원 | 전면 지원 |

### 1.2 왜 OpenRewrite인가?

- **자동화된 코드 변환**: 수천 개 파일의 API 변경을 자동 적용
- **안전한 AST 기반 변환**: 텍스트 치환이 아닌 구문 트리 기반 정확한 변환
- **검증된 레시피**: Java, Spring, Jakarta EE 등 업계 표준 마이그레이션 레시피 제공
- **Dry-run 지원**: 실제 변경 전 영향 범위 사전 확인 가능
- **빌드 도구 통합**: Maven/Gradle 플러그인으로 기존 빌드 파이프라인에 통합

### 1.3 마이그레이션 경로

Java 버전 간 변화가 크므로, **단계적 업그레이드**를 권장합니다:

```
Java 7 → Java 8 → Java 11 → Java 17 → Java 21
```

각 단계에서 주요 변경사항:

| 단계 | 핵심 변경사항 |
|------|---------------|
| 7 → 8 | Lambda, Stream API, Optional, Date/Time API |
| 8 → 11 | 모듈 시스템(JPMS), J2EE 모듈 제거(JAXB, JAX-WS 등), var 키워드 |
| 11 → 17 | Sealed Classes, Records, Pattern Matching, Text Blocks, 강화된 NullPointerException |
| 17 → 21 | Virtual Threads, Sequenced Collections, Record Patterns, Switch Pattern Matching |

---

## 2. 마이그레이션 전략

### 2.1 전체 작업 흐름 (2단계 접근)

```
┌─────────────────────────────────────────────────────┐
│              인터넷 환경 (개발 PC/서버)                 │
│                                                     │
│  1. JDK 21 다운로드                                   │
│  2. Maven 3.9.x 다운로드                              │
│  3. OpenRewrite 플러그인 + 의존성 전체 다운로드            │
│  4. 프로젝트 의존성 오프라인 저장소 구성                    │
│  5. Dry-run 테스트 실행 및 검증                         │
│  6. 전체 패키지를 보안매체로 복사                         │
│                                                     │
└──────────────────────┬──────────────────────────────┘
                       │ USB/보안매체
┌──────────────────────▼──────────────────────────────┐
│              폐쇄망 환경 (금융 운영 서버)                 │
│                                                     │
│  7. JDK 21 설치                                      │
│  8. Maven 로컬 저장소 배치                              │
│  9. settings.xml 오프라인 구성                          │
│  10. OpenRewrite 레시피 실행                            │
│  11. 수동 보완 작업                                    │
│  12. 빌드 및 테스트                                    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 2.2 OpenRewrite 레시피 체인

| 순서 | 레시피 ID | 설명 | 아티팩트 |
|------|-----------|------|----------|
| 1 | `org.openrewrite.java.migrate.UpgradeToJava8` | Java 7→8 API 변환 | `rewrite-migrate-java` |
| 2 | `org.openrewrite.java.migrate.Java8toJava11` | Java 8→11, J2EE 모듈 제거 대응 | `rewrite-migrate-java` |
| 3 | `org.openrewrite.java.migrate.UpgradeToJava17` | Java 11→17 API 변환 | `rewrite-migrate-java` |
| 4 | `org.openrewrite.java.migrate.UpgradeToJava21` | Java 17→21 최종 변환 | `rewrite-migrate-java` |
| 5 | (조건부) `org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0` | Spring Boot 2→3 (eGov 5.0 대응 시) | `rewrite-spring` |
| 6 | (조건부) `org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta` | javax→jakarta 네임스페이스 변환 | `rewrite-migrate-java` |

---

## 3. eGovFramework 호환성 분석

### 3.1 현황 요약

| 항목 | 내용 |
|------|------|
| **현재 최신 버전** | eGovFramework 4.3.0 (2024) |
| **기반 Spring Boot** | 2.7.18 (Spring Framework 5.3.37) |
| **런타임 JDK 요구사항** | JDK 8 이상 |
| **개발환경 JDK** | JDK 17 (Eclipse 2024-03 기반), Open JDK 21 사용 확인 |
| **Java 21 공식 지원** | eGovFramework 5.0에서 기대 (2025.12 베타 배포) |
| **Spring Boot 3.x 지원** | 4.x에서는 미지원, 5.0에서 전환 예상 |

### 3.2 eGovFramework 버전별 의존성 매핑

```
eGov 3.x  → Spring 4.x  → Java 7/8   → javax.*
eGov 4.0  → Spring 5.3  → Java 8+    → javax.*  (패키지명 변경: egovframework.rte → org.egovframe.rte)
eGov 4.3  → Spring 5.3  → Java 8+    → javax.*  (Spring Boot 2.7.18)
eGov 5.0  → Spring 6.x  → Java 17+   → jakarta.* (Spring Boot 3.x 기반, 2025.12 베타)
```

### 3.3 마이그레이션 시나리오별 전략

#### 시나리오 A: eGovFramework 유지 + Java 버전만 올리기

eGov 4.3은 공식적으로 JDK 8+를 지원하므로, **런타임 JDK만 21로 교체**하는 것은 이론적으로 가능합니다.

```
현재: eGov 4.3 + JDK 7 + Spring Boot 2.7
목표: eGov 4.3 + JDK 21 + Spring Boot 2.7
```

**장점**: 변경 범위 최소화
**위험**: Spring Boot 2.7은 JDK 21에서 일부 호환성 이슈 가능 (리플렉션 접근 제한 등)
**필요 작업**:
- `--add-opens` JVM 옵션 추가 (모듈 시스템 접근 허용)
- Deprecated API 호출 점검
- 바이트코드 호환성 테스트

#### 시나리오 B: eGovFramework 5.0으로 업그레이드 (권장)

eGov 5.0 정식 출시 시, Spring Boot 3.x 기반으로 Java 21을 완전 지원할 것으로 기대됩니다.

```
현재: eGov 3.x/4.x + JDK 7 + Spring 4.x/5.x
목표: eGov 5.0 + JDK 21 + Spring Boot 3.x
```

**장점**: 공식 지원, 장기 유지보수 가능
**위험**: eGov 5.0 정식 출시 일정 불확실
**필요 작업**:
- eGov 5.0 베타 검증
- javax → jakarta 네임스페이스 전환 (OpenRewrite 자동화 가능)
- eGov RTE 라이브러리 교체

#### 시나리오 C: eGovFramework 탈피 → 순수 Spring Boot 3.x

eGov 의존성을 제거하고 순수 Spring Boot 3.x로 전환합니다.

```
현재: eGov 3.x/4.x + JDK 7
목표: Spring Boot 3.x + JDK 21 (eGov 비의존)
```

**장점**: 최신 생태계 완전 활용, 유지보수 용이
**위험**: 변경 범위 가장 큼, eGov 호환성 인증 불가
**필요 작업**:
- `EgovAbstractServiceImpl` 등 eGov RTE 클래스 제거/대체
- XML 기반 Bean 설정 → Java Config 전환
- eGov 공통 컴포넌트 대체 구현
- OpenRewrite `UpgradeSpringBoot_3_0` 레시피 활용

### 3.4 OpenRewrite로 eGovFramework 관련 자동화 가능 범위

| 작업 | OpenRewrite 자동화 | 비고 |
|------|:---:|------|
| Java API 변경 (7→21) | ✅ | `rewrite-migrate-java` 레시피 |
| javax → jakarta 변환 | ✅ | `JavaxMigrationToJakarta` 레시피 |
| Spring Boot 2→3 마이그레이션 | ✅ | `UpgradeSpringBoot_3_0` 레시피 |
| Spring Security 마이그레이션 | ✅ | `UpgradeSpringBoot_3_0`에 포함 |
| pom.xml 의존성 버전 업데이트 | ✅ | 자동 적용 |
| Maven 컴파일러 플러그인 업데이트 | ✅ | 자동 적용 |
| eGov RTE 패키지 경로 변경 | ⚠️ 커스텀 레시피 필요 | `ChangePackage` 레시피 활용 가능 |
| eGov XML 설정 → Java Config | ❌ 수동 | OpenRewrite로 부분 자동화 가능하나 한계 있음 |
| eGov 공통 컴포넌트 대체 | ❌ 수동 | 비즈니스 로직 의존 |
| WAS 호환성 (JEUS, WebLogic) | ❌ 수동 | 별도 테스트 필요 |

### 3.5 커스텀 OpenRewrite 레시피 (eGov 전용)

eGov 관련 반복적인 변환을 자동화하기 위해 커스텀 레시피를 작성할 수 있습니다:

```yaml
# rewrite.yml (프로젝트 루트에 배치)
---
type: specs.openrewrite.org/v1beta/recipe
name: com.example.EgovMigration
displayName: eGovFramework Migration Recipes
description: eGovFramework 전용 마이그레이션 레시피
recipeList:
  # 1. eGov 3.x 패키지 경로 → 4.x 경로 변환
  - org.openrewrite.java.ChangePackage:
      oldPackageName: egovframework.rte
      newPackageName: org.egovframe.rte
      recursive: true

  # 2. Deprecated eGov API 교체 (예시)
  - org.openrewrite.java.ChangeType:
      oldFullyQualifiedTypeName: egovframework.rte.fdl.cmmn.EgovAbstractServiceImpl
      newFullyQualifiedTypeName: org.egovframe.rte.fdl.cmmn.EgovAbstractServiceImpl

  # 3. Spring XML 네임스페이스 업데이트
  - org.openrewrite.xml.ChangeTagAttribute:
      elementName: beans
      attributeName: xmlns
      oldValue: http://www.springframework.org/schema/beans/spring-beans-4.0.xsd
      newValue: http://www.springframework.org/schema/beans/spring-beans.xsd

  # 4. Java 버전 업그레이드
  - org.openrewrite.java.migrate.UpgradeToJava21

  # 5. (필요 시) javax → jakarta
  - org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta
```

---

## 4. Phase 1 — 인터넷 환경 준비 (오프라인 패키지 구성)

### 4.1 필수 다운로드 목록

#### A. JDK 21 (설치 파일)

| 배포판 | 다운로드 URL | 비고 |
|--------|-------------|------|
| Eclipse Temurin 21 | https://adoptium.net/temurin/releases/ | 가장 범용적 |
| Amazon Corretto 21 | https://docs.aws.amazon.com/corretto/latest/userguide/downloads-list.html | AWS 환경 시 |
| Red Hat OpenJDK 21 | https://developers.redhat.com/products/openjdk/download | RHEL 환경 시 |

> **다운로드 대상 OS**: 폐쇄망 서버의 OS에 맞는 바이너리 (Linux x64, Windows x64 등)

#### B. Apache Maven 3.9.x

```bash
# 최신 Maven 3.9.x 다운로드
wget https://dlcdn.apache.org/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.tar.gz
```

#### C. OpenRewrite 플러그인 및 레시피 아티팩트

**핵심 아티팩트 (Maven Central에서 다운로드)**:

| GroupId | ArtifactId | Version | 용도 |
|---------|------------|---------|------|
| `org.openrewrite.maven` | `rewrite-maven-plugin` | `6.28.1` | Maven 플러그인 |
| `org.openrewrite.recipe` | `rewrite-migrate-java` | `3.26.0` | Java 마이그레이션 레시피 |
| `org.openrewrite.recipe` | `rewrite-spring` | `5.27.0` | Spring 마이그레이션 레시피 (필요 시) |
| `org.openrewrite.recipe` | `rewrite-recipe-bom` | `3.5.0` | BOM (버전 관리) |
| `org.openrewrite` | `rewrite-bom` | `8.72.0` | 코어 BOM |

### 4.2 Maven 오프라인 저장소 구성

#### Step 1: 임시 프로젝트 생성 (인터넷 환경)

인터넷이 가능한 환경에서 대상 프로젝트와 동일한 구조의 pom.xml을 준비합니다.

```xml
<!-- offline-prep/pom.xml -->
<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>openrewrite-offline-prep</artifactId>
    <version>1.0.0</version>
    <packaging>pom</packaging>

    <properties>
        <maven.compiler.source>7</maven.compiler.source>
        <maven.compiler.target>7</maven.compiler.target>
    </properties>

    <build>
        <plugins>
            <!-- OpenRewrite Maven Plugin -->
            <plugin>
                <groupId>org.openrewrite.maven</groupId>
                <artifactId>rewrite-maven-plugin</artifactId>
                <version>6.28.1</version>
                <configuration>
                    <exportDatatables>true</exportDatatables>
                    <activeRecipes>
                        <recipe>org.openrewrite.java.migrate.UpgradeToJava21</recipe>
                    </activeRecipes>
                </configuration>
                <dependencies>
                    <dependency>
                        <groupId>org.openrewrite.recipe</groupId>
                        <artifactId>rewrite-migrate-java</artifactId>
                        <version>3.26.0</version>
                    </dependency>
                </dependencies>
            </plugin>

            <!-- Go-Offline Plugin (모든 의존성 다운로드) -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-dependency-plugin</artifactId>
                <version>3.6.1</version>
            </plugin>
        </plugins>
    </build>
</project>
```

#### Step 2: 모든 의존성 다운로드

```bash
# 1. 프로젝트 의존성 + 플러그인 의존성 모두 다운로드
mvn dependency:go-offline -Dmaven.repo.local=./offline-repo

# 2. OpenRewrite 플러그인 의존성 해결
mvn org.openrewrite.maven:rewrite-maven-plugin:6.28.1:dryRun \
    -Dmaven.repo.local=./offline-repo

# 3. 추가 레시피 의존성 (Spring Boot 마이그레이션 포함 시)
mvn dependency:resolve-plugins -Dmaven.repo.local=./offline-repo
```

#### Step 3: 실제 프로젝트의 의존성도 포함

```bash
# 실제 프로젝트 디렉토리에서 실행
cd /path/to/actual-project

# 기존 프로젝트의 모든 의존성도 오프라인 저장소에 포함
mvn dependency:go-offline -Dmaven.repo.local=/path/to/offline-repo

# 의존성 트리 확인 및 저장
mvn dependency:tree -Dmaven.repo.local=/path/to/offline-repo > dependency-tree.txt
```

#### Step 4: go-offline-maven-plugin 활용 (더 완전한 방법)

```xml
<!-- pom.xml에 추가 -->
<plugin>
    <groupId>de.qaware.maven</groupId>
    <artifactId>go-offline-maven-plugin</artifactId>
    <version>1.2.8</version>
    <configuration>
        <dynamicDependencies>
            <DynamicDependency>
                <groupId>org.openrewrite.maven</groupId>
                <artifactId>rewrite-maven-plugin</artifactId>
                <version>6.28.1</version>
                <repositoryType>PLUGIN</repositoryType>
            </DynamicDependency>
            <DynamicDependency>
                <groupId>org.openrewrite.recipe</groupId>
                <artifactId>rewrite-migrate-java</artifactId>
                <version>3.26.0</version>
                <repositoryType>MAIN</repositoryType>
            </DynamicDependency>
        </dynamicDependencies>
    </configuration>
</plugin>
```

```bash
# 실행
mvn de.qaware.maven:go-offline-maven-plugin:resolve-dependencies \
    -Dmaven.repo.local=./offline-repo
```

#### Step 5: 오프라인 저장소 패키징

```bash
# 오프라인 저장소 압축
tar -czf openrewrite-offline-repo.tar.gz offline-repo/

# 크기 확인 (일반적으로 500MB ~ 2GB)
du -sh offline-repo/

# 체크섬 생성 (무결성 검증용)
sha256sum openrewrite-offline-repo.tar.gz > openrewrite-offline-repo.tar.gz.sha256
```

### 4.3 인터넷 환경 Dry-Run 테스트

폐쇄망 이관 전에 반드시 인터넷 환경에서 테스트합니다:

```bash
# Dry-Run: 실제 코드 변경 없이 변환 결과 미리보기
mvn rewrite:dryRun \
    -Dmaven.repo.local=./offline-repo

# 결과 확인
cat target/rewrite/rewrite.patch

# 변경 사항 리포트 확인
ls -la target/rewrite/
```

### 4.4 보안매체 이관 패키지 목록

폐쇄망으로 이관해야 할 파일 목록:

```
transfer-package/
├── jdk/
│   └── OpenJDK21U-jdk_x64_linux_hotspot_21.0.x.tar.gz
├── maven/
│   └── apache-maven-3.9.9-bin.tar.gz
├── offline-repo/
│   └── openrewrite-offline-repo.tar.gz     # Maven 로컬 저장소 전체
├── config/
│   ├── settings.xml                         # 오프라인 Maven 설정
│   └── rewrite.yml                          # 커스텀 레시피 (eGov 등)
├── scripts/
│   ├── 01-install-jdk.sh
│   ├── 02-setup-maven.sh
│   ├── 03-run-migration.sh
│   └── 04-verify.sh
├── docs/
│   └── JAVA_UPGRADE_PLAN.md                 # 이 문서
└── checksums/
    └── SHA256SUMS.txt                        # 전체 파일 체크섬
```

---

## 5. Phase 2 — 폐쇄망 환경 설정

### 5.1 JDK 21 설치

```bash
# 1. JDK 압축 해제
tar -xzf OpenJDK21U-jdk_x64_linux_hotspot_21.0.x.tar.gz -C /opt/

# 2. 환경변수 설정
export JAVA_HOME=/opt/jdk-21
export PATH=$JAVA_HOME/bin:$PATH

# 3. 검증
java -version
# openjdk version "21.0.x" ...

# 4. 영구 설정 (필요 시)
echo 'export JAVA_HOME=/opt/jdk-21' >> /etc/profile.d/java.sh
echo 'export PATH=$JAVA_HOME/bin:$PATH' >> /etc/profile.d/java.sh
```

### 5.2 Maven 설정

```bash
# 1. Maven 설치
tar -xzf apache-maven-3.9.9-bin.tar.gz -C /opt/
export M2_HOME=/opt/apache-maven-3.9.9
export PATH=$M2_HOME/bin:$PATH

# 2. 검증
mvn -version
```

### 5.3 오프라인 저장소 배치

```bash
# 1. 오프라인 저장소 압축 해제
tar -xzf openrewrite-offline-repo.tar.gz -C /opt/maven-offline-repo/

# 2. 체크섬 검증
sha256sum -c SHA256SUMS.txt
```

### 5.4 Maven settings.xml (오프라인 모드)

```xml
<!-- ~/.m2/settings.xml -->
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">

    <!-- 오프라인 모드 활성화 -->
    <offline>true</offline>

    <!-- 로컬 저장소 경로 지정 -->
    <localRepository>/opt/maven-offline-repo/offline-repo</localRepository>

    <!-- 모든 원격 저장소 미러를 로컬로 지정 (혹시 offline=false 시 fallback) -->
    <mirrors>
        <mirror>
            <id>offline-central</id>
            <mirrorOf>*</mirrorOf>
            <url>file:///opt/maven-offline-repo/offline-repo</url>
        </mirror>
    </mirrors>

    <profiles>
        <profile>
            <id>offline</id>
            <activation>
                <activeByDefault>true</activeByDefault>
            </activation>
            <repositories>
                <repository>
                    <id>local-repo</id>
                    <url>file:///opt/maven-offline-repo/offline-repo</url>
                    <releases><enabled>true</enabled></releases>
                    <snapshots><enabled>false</enabled></snapshots>
                </repository>
            </repositories>
            <pluginRepositories>
                <pluginRepository>
                    <id>local-plugin-repo</id>
                    <url>file:///opt/maven-offline-repo/offline-repo</url>
                    <releases><enabled>true</enabled></releases>
                    <snapshots><enabled>false</enabled></snapshots>
                </pluginRepository>
            </pluginRepositories>
        </profile>
    </profiles>
</settings>
```

### 5.5 Nexus Repository 활용 (권장 — 대규모 환경)

폐쇄망에 Nexus Repository Manager가 있는 경우:

```bash
# Nexus hosted repository에 오프라인 저장소 업로드
# Nexus Admin UI에서 hosted repository 생성 후:

# 방법 1: 디렉토리 통째로 Nexus storage에 배치
cp -r offline-repo/* /opt/nexus/sonatype-work/nexus3/blobs/default/

# 방법 2: REST API를 통한 업로드 (스크립트)
for file in $(find offline-repo -name "*.jar" -o -name "*.pom"); do
    # GAV 추출 후 업로드
    curl -u admin:admin123 \
        --upload-file "$file" \
        "http://nexus-host:8081/repository/maven-hosted/$(echo $file | sed 's|offline-repo/||')"
done
```

---

## 6. Phase 3 — 단계별 마이그레이션 실행

### 6.1 사전 준비

```bash
# 1. 프로젝트 백업 (필수!)
cp -r /path/to/project /path/to/project-backup-$(date +%Y%m%d)

# 2. Git 브랜치 생성
cd /path/to/project
git checkout -b feature/java21-migration

# 3. 현재 상태 확인
java -version
mvn -version
mvn clean compile  # 기존 JDK로 빌드 확인
```

### 6.2 Step 1 — Java 7 → 8 마이그레이션

```xml
<!-- pom.xml에 OpenRewrite 플러그인 추가 -->
<build>
    <plugins>
        <plugin>
            <groupId>org.openrewrite.maven</groupId>
            <artifactId>rewrite-maven-plugin</artifactId>
            <version>6.28.1</version>
            <configuration>
                <activeRecipes>
                    <recipe>org.openrewrite.java.migrate.UpgradeToJava8</recipe>
                </activeRecipes>
            </configuration>
            <dependencies>
                <dependency>
                    <groupId>org.openrewrite.recipe</groupId>
                    <artifactId>rewrite-migrate-java</artifactId>
                    <version>3.26.0</version>
                </dependency>
            </dependencies>
        </plugin>
    </plugins>
</build>
```

```bash
# Dry-Run (영향 범위 확인)
mvn rewrite:dryRun -o   # -o : offline mode

# 변경 사항 확인
cat target/rewrite/rewrite.patch

# 문제 없으면 실행
mvn rewrite:run -o

# 결과 확인
git diff --stat
git diff

# 빌드 테스트
mvn clean compile -o

# 커밋
git add -A
git commit -m "chore: migrate Java 7 to Java 8 via OpenRewrite"
```

**주요 변환 내용**:
- `diamond operator` 적용: `new ArrayList<String>()` → `new ArrayList<>()`
- `try-with-resources` 적용
- 일부 Deprecated API 교체

### 6.3 Step 2 — Java 8 → 11 마이그레이션

```xml
<!-- activeRecipes 변경 -->
<recipe>org.openrewrite.java.migrate.Java8toJava11</recipe>
```

```bash
mvn rewrite:dryRun -o
cat target/rewrite/rewrite.patch
mvn rewrite:run -o
mvn clean compile -o
git add -A
git commit -m "chore: migrate Java 8 to Java 11 via OpenRewrite"
```

**주요 변환 내용**:
- J2EE 모듈 제거 대응 (JAXB, JAX-WS 의존성 추가)
- `javax.xml.bind` → 별도 의존성으로 분리
- `sun.misc.BASE64Encoder` → `java.util.Base64`

> ⚠️ **eGov 주의**: eGov가 내부적으로 JAXB를 사용하는 경우, 명시적 의존성 추가 필요:
> ```xml
> <dependency>
>     <groupId>jakarta.xml.bind</groupId>
>     <artifactId>jakarta.xml.bind-api</artifactId>
>     <version>4.0.2</version>
> </dependency>
> <dependency>
>     <groupId>org.glassfish.jaxb</groupId>
>     <artifactId>jaxb-runtime</artifactId>
>     <version>4.0.5</version>
> </dependency>
> ```

### 6.4 Step 3 — Java 11 → 17 마이그레이션

```xml
<recipe>org.openrewrite.java.migrate.UpgradeToJava17</recipe>
```

```bash
mvn rewrite:dryRun -o
mvn rewrite:run -o
mvn clean compile -o
git add -A
git commit -m "chore: migrate Java 11 to Java 17 via OpenRewrite"
```

**주요 변환 내용**:
- 강화된 캡슐화 대응 (internal API 접근 제한)
- Deprecated API 제거/교체
- `maven.compiler.source/target` → 17로 업데이트

> ⚠️ **JVM 옵션 필요 시**:
> ```bash
> # Spring Boot 2.7 + JDK 17 에서 리플렉션 관련 경고 해결
> export JAVA_OPTS="--add-opens java.base/java.lang=ALL-UNNAMED \
>     --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
>     --add-opens java.base/java.util=ALL-UNNAMED"
> ```

### 6.5 Step 4 — Java 17 → 21 마이그레이션

```xml
<recipe>org.openrewrite.java.migrate.UpgradeToJava21</recipe>
```

```bash
mvn rewrite:dryRun -o
mvn rewrite:run -o
mvn clean compile -o
git add -A
git commit -m "chore: migrate Java 17 to Java 21 via OpenRewrite"
```

**주요 변환 내용**:
- `maven.compiler.source/target` → 21로 업데이트
- Deprecated API 최종 정리
- 최신 API 권장사항 적용

### 6.6 Step 5 — (선택) Spring Boot 2 → 3 마이그레이션

eGov 5.0으로 전환하거나 순수 Spring Boot 3로 전환하는 경우:

```xml
<!-- 추가 레시피 의존성 -->
<dependency>
    <groupId>org.openrewrite.recipe</groupId>
    <artifactId>rewrite-spring</artifactId>
    <version>5.27.0</version>
</dependency>

<!-- 레시피 변경 -->
<recipe>org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0</recipe>
```

```bash
mvn rewrite:dryRun -o
mvn rewrite:run -o

# javax → jakarta 변환 확인
grep -r "javax\." src/ --include="*.java" | grep -v "javax.annotation.processing"
```

### 6.7 Step 6 — 수동 보완 작업

OpenRewrite가 자동으로 처리하지 못하는 항목들:

```
□ eGov RTE 라이브러리 호환성 확인 및 버전 업데이트
□ eGov 공통 컴포넌트 (254종) 동작 확인
□ XML 기반 Bean 설정에서 Spring 스키마 버전 업데이트
□ WAS 배포 디스크립터 (web.xml) 서블릿 스펙 버전 확인
□ 로깅 프레임워크 호환성 (Log4j 2.x 확인)
□ JDBC 드라이버 업그레이드 (Oracle, Tibero 등)
□ 암호화/보안 라이브러리 호환성 (BouncyCastle, ARIA 등)
□ JNI/Native 라이브러리 호환성 확인
□ 프로파일링/모니터링 에이전트 호환성 (Jennifer, Scouter 등)
```

---

## 7. Phase 4 — 검증 및 테스트

### 7.1 빌드 검증

```bash
# 클린 빌드
mvn clean package -o -DskipTests

# 전체 테스트 실행
mvn clean verify -o

# 빌드 결과물 확인
ls -la target/*.war  # 또는 *.jar
```

### 7.2 자동화 테스트

```bash
# 단위 테스트
mvn test -o

# 통합 테스트
mvn verify -o -Pintegration-test

# 코드 커버리지 확인 (JaCoCo)
mvn jacoco:report -o
```

### 7.3 런타임 검증 체크리스트

| 검증 항목 | 확인 방법 | 상태 |
|-----------|----------|------|
| 애플리케이션 정상 기동 | WAS 배포 후 로그 확인 | □ |
| 주요 화면 접근 | 브라우저에서 주요 URL 테스트 | □ |
| 로그인/인증 | 세션/토큰 기반 인증 동작 확인 | □ |
| DB CRUD 동작 | 게시판 등 기본 기능 테스트 | □ |
| 배치 작업 | Spring Batch 정상 실행 확인 | □ |
| 암/복호화 | 개인정보 암호화 동작 확인 | □ |
| 파일 업/다운로드 | 첨부파일 기능 확인 | □ |
| 외부 연동 | 대외계/대내계 인터페이스 확인 | □ |
| 성능 | 응답시간 기준치 대비 비교 | □ |
| GC 로그 | `-Xlog:gc*` 옵션으로 GC 동작 확인 | □ |
| 메모리 사용량 | JDK 21 기본 GC 최적화 확인 | □ |

### 7.4 JVM 옵션 정리 (JDK 21 권장)

```bash
# 기존 JDK 7 옵션에서 제거해야 할 항목
# -XX:MaxPermSize (Metaspace로 대체됨)
# -XX:PermSize (Metaspace로 대체됨)

# JDK 21 권장 옵션
JAVA_OPTS="\
    -Xms2g \
    -Xmx4g \
    -XX:MetaspaceSize=256m \
    -XX:MaxMetaspaceSize=512m \
    -XX:+UseZGC \
    -XX:+ZGenerational \
    -Xlog:gc*:file=/logs/gc.log:time,uptime,level,tags \
    --add-opens java.base/java.lang=ALL-UNNAMED \
    --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
    --add-opens java.base/java.io=ALL-UNNAMED \
    --add-opens java.base/java.util=ALL-UNNAMED \
    --add-opens java.base/java.text=ALL-UNNAMED \
    --add-opens java.desktop/java.awt.font=ALL-UNNAMED \
    -Djava.security.egd=file:/dev/./urandom"
```

---

## 8. pom.xml 기반 의존성 영향도 분석 방법

### 8.1 OpenRewrite 마이그레이션 영향도 사전 분석

pom.xml을 제공하면 다음과 같은 분석이 가능합니다:

#### A. 의존성 Java 21 호환성 분석

```bash
# 1. 의존성 트리 전체 출력
mvn dependency:tree > dependency-tree.txt

# 2. OpenRewrite 마이그레이션 계획 레시피 실행
# (실제 변경 없이 영향도만 분석)
```

```xml
<!-- 영향도 분석 전용 레시피 -->
<recipe>org.openrewrite.java.migrate.search.PlanJavaMigration</recipe>
```

```bash
mvn rewrite:dryRun -o
# 결과: target/rewrite/rewrite.patch 에 모든 변경 예정 사항 출력
```

#### B. 분석 가능 항목

| 분석 항목 | 방법 | 자동화 |
|-----------|------|:------:|
| 직접 의존성 Java 21 호환성 | MVN Repository 버전 확인 | 반자동 |
| Transitive 의존성 충돌 | `mvn dependency:tree -Dverbose` | ✅ |
| Deprecated API 사용 현황 | `jdeprscan --release 21 target/*.jar` | ✅ |
| 내부 API 사용 여부 | `jdeps --jdk-internals target/*.jar` | ✅ |
| 멀티릴리즈 JAR 지원 여부 | `jar --describe-module` | ✅ |
| OpenRewrite 변환 대상 코드 | `rewrite:dryRun` | ✅ |

#### C. jdeps / jdeprscan 활용 (JDK 21 내장 도구)

```bash
# 1. JDK 내부 API 사용 탐지
jdeps --jdk-internals \
    --multi-release 21 \
    target/your-app.war

# 2. Deprecated API 사용 탐지
jdeprscan --release 21 target/your-app.jar

# 3. 모듈 의존성 분석
jdeps --module-path $JAVA_HOME/jmods \
    --print-module-deps \
    target/your-app.jar
```

### 8.2 pom.xml 분석 시 제공해야 할 정보

분석의 정확도를 높이기 위해 다음 정보를 함께 제공하면 좋습니다:

```
필수:
  □ pom.xml (부모 pom 포함, 멀티모듈 시 전체)
  □ 현재 Java 버전 (소스/타겟)
  □ 현재 사용 중인 WAS 종류 및 버전

권장:
  □ eGovFramework 버전
  □ Spring/Spring Boot 버전
  □ mvn dependency:tree 실행 결과
  □ 특수 라이브러리 목록 (암호화, 전자서명, 본인인증 등)
  □ JNI/Native 라이브러리 사용 여부
  □ 빌드 프로파일 정보
```

### 8.3 주요 의존성별 Java 21 호환 버전 참고

| 라이브러리 | Java 7 지원 최종 버전 | Java 21 지원 최소 버전 |
|-----------|---------------------|---------------------|
| Spring Framework | 4.3.30 | 6.0.0+ (Java 17+) |
| Spring Boot | 1.5.22 | 3.0.0+ (Java 17+) |
| Hibernate | 4.3.11 | 6.2+ (Java 11+) |
| MyBatis | 3.4.6 | 3.5.11+ |
| Log4j 2 | 2.12.4 (Java 7) | 2.17.1+ |
| Jackson | 2.9.x | 2.15+ |
| Lombok | 1.18.x | 1.18.30+ |
| JUnit | 4.13 | 5.10+ (JUnit 5) |
| Mockito | 2.x | 5.x+ |
| Apache Commons Lang | 3.8 | 3.13+ |
| Apache Commons IO | 2.6 | 2.14+ |
| Guava | 25.x | 32+ |
| BouncyCastle | 1.60 | 1.77+ |
| Oracle JDBC | ojdbc7 | ojdbc11 (21.x) |
| Tibero JDBC | tbJDBC6 | tbJDBC8+ (확인 필요) |
| eGovFramework | 3.x | 5.0+ (출시 대기) |

---

## 9. 롤백 계획

### 9.1 롤백 시나리오

```
Level 1: 코드 롤백 (Git)
  └─ git checkout main  # 마이그레이션 이전 브랜치로 복귀

Level 2: JDK 롤백
  └─ JAVA_HOME을 JDK 7 경로로 복원

Level 3: 전체 롤백
  └─ 백업본에서 프로젝트 디렉토리 전체 복원
  └─ WAS에 이전 배포본 재배포
```

### 9.2 롤백 절차

```bash
# 1. Git 기반 코드 롤백
git stash          # 현재 작업 임시 저장
git checkout main  # 이전 상태로 복귀

# 2. JDK 롤백
export JAVA_HOME=/opt/jdk-1.7
export PATH=$JAVA_HOME/bin:$PATH

# 3. 빌드 및 배포 복원
mvn clean package -o
# WAS 재배포
```

---

## 10. 금융권 특수 고려사항

### 10.1 보안 관련

| 항목 | 내용 | 조치 |
|------|------|------|
| TLS 버전 | JDK 21 기본: TLS 1.3 | 대외계 연동 시 TLS 버전 호환성 확인 |
| 암호화 알고리즘 | JDK 21에서 일부 알고리즘 제거 | ARIA, SEED 등 국내 알고리즘 라이브러리 호환성 확인 |
| 보안 모듈 | 공인인증서, HSM, PKI | Native 바인딩 Java 21 호환성 확인 |
| 키 길이 | JDK 21 기본 보안 정책 강화 | `java.security` 설정 검토 |
| SecureRandom | JDK 21 기본 구현 변경 | 성능 영향 확인 (`-Djava.security.egd`) |

### 10.2 WAS 호환성

| WAS | Java 21 지원 버전 |
|-----|-------------------|
| JEUS 8 | JEUS 8 Fix#6 이상 (확인 필요) |
| WebLogic | 14.1.2+ |
| Tomcat | 10.1+ (Jakarta EE), 9.0.x (javax) |
| WildFly | 30+ |

> ⚠️ **JEUS 사용 시**: TmaxSoft에 Java 21 호환 패치 여부를 반드시 사전 확인하세요.

### 10.3 모니터링/APM 도구

| 도구 | Java 21 지원 버전 |
|------|-------------------|
| Jennifer | 5.6+ (확인 필요) |
| Scouter | 2.20+ |
| Pinpoint | 3.0+ |
| WhaTap | 최신 에이전트 |

> 각 APM 벤더에 Java 21 호환 에이전트 버전을 사전 확인하고, 해당 에이전트도 보안매체로 함께 이관해야 합니다.

### 10.4 작업 일정 권장안

```
Week 1-2:  인터넷 환경 준비 (다운로드, Dry-Run, 패키징)
Week 3:    폐쇄망 이관 및 환경 설정 (보안 심사 포함)
Week 4-5:  OpenRewrite 실행 + 수동 보완
Week 6-7:  단위/통합 테스트
Week 8:    성능 테스트 + 보안 점검
Week 9:    스테이징 환경 배포 및 UAT
Week 10:   운영 배포 (야간/주말)
```

---

## 11. 체크리스트 (전체)

### Phase 1: 인터넷 환경 준비

- [ ] JDK 21 설치파일 다운로드 (폐쇄망 서버 OS에 맞는 버전)
- [ ] Apache Maven 3.9.x 다운로드
- [ ] OpenRewrite pom.xml 구성 (rewrite-maven-plugin 6.28.1)
- [ ] rewrite-migrate-java 3.26.0 의존성 추가
- [ ] (선택) rewrite-spring 5.27.0 의존성 추가
- [ ] `mvn dependency:go-offline` 실행하여 전체 의존성 다운로드
- [ ] 실제 프로젝트 pom.xml 기반 의존성도 함께 다운로드
- [ ] Dry-Run 테스트 실행 및 결과 검토
- [ ] 커스텀 레시피 파일(rewrite.yml) 작성 (eGov 전용)
- [ ] 오프라인 저장소 압축 및 체크섬 생성
- [ ] 보안매체 이관 패키지 구성
- [ ] 이관 패키지 보안 심사 신청 (매체 반입 승인)

### Phase 2: 폐쇄망 환경 설정

- [ ] 보안매체 수령 및 무결성 검증 (SHA256)
- [ ] JDK 21 설치 및 환경변수 설정
- [ ] Maven 3.9.x 설치 및 환경변수 설정
- [ ] 오프라인 Maven 저장소 배치
- [ ] settings.xml 오프라인 모드 구성
- [ ] (선택) Nexus hosted repository에 업로드
- [ ] `mvn -version`, `java -version` 검증
- [ ] 오프라인 빌드 테스트 (`mvn clean compile -o`)

### Phase 3: 마이그레이션 실행

- [ ] 프로젝트 전체 백업 (디렉토리 복사 + Git 태그)
- [ ] 마이그레이션 브랜치 생성
- [ ] Step 1: Java 7→8 레시피 실행 + 빌드 확인
- [ ] Step 2: Java 8→11 레시피 실행 + 빌드 확인
- [ ] Step 3: Java 11→17 레시피 실행 + 빌드 확인
- [ ] Step 4: Java 17→21 레시피 실행 + 빌드 확인
- [ ] (선택) Step 5: Spring Boot 3 마이그레이션 레시피 실행
- [ ] (선택) Step 6: javax→jakarta 변환 레시피 실행
- [ ] 수동 보완 작업 (eGov RTE, XML 설정 등)
- [ ] 컴파일 경고 0건 확인

### Phase 4: 검증 및 테스트

- [ ] 단위 테스트 전체 통과
- [ ] 통합 테스트 전체 통과
- [ ] WAS 배포 및 기동 확인
- [ ] 주요 화면/기능 수동 테스트
- [ ] DB CRUD 동작 확인
- [ ] 배치 작업 동작 확인
- [ ] 암/복호화 동작 확인
- [ ] 대외계/대내계 연동 확인
- [ ] 성능 테스트 (응답시간 기준치 대비)
- [ ] GC 로그 분석 및 JVM 튜닝
- [ ] 보안 점검 (TLS, 암호화 알고리즘)
- [ ] 모니터링/APM 에이전트 동작 확인

### Phase 5: 배포

- [ ] 스테이징 환경 배포 및 UAT
- [ ] 운영 배포 계획서 작성
- [ ] 롤백 절차 사전 리허설
- [ ] 운영 배포 실행
- [ ] 배포 후 모니터링 (1주일)

---

## 12. 참고 자료

### OpenRewrite 공식 문서
- [Migrate to Java 21](https://docs.openrewrite.org/recipes/java/migrate/upgradetojava21)
- [Migrate to Java 17](https://docs.openrewrite.org/running-recipes/popular-recipe-guides/migrate-to-java-17)
- [Migrate to Spring Boot 3](https://docs.openrewrite.org/running-recipes/popular-recipe-guides/migrate-to-spring-3)
- [Plan Java Migration](https://docs.openrewrite.org/recipes/java/migrate/search/planjavamigration)
- [Maven Plugin Configuration](https://docs.openrewrite.org/reference/rewrite-maven-plugin)

### OpenRewrite GitHub
- [rewrite-migrate-java](https://github.com/openrewrite/rewrite-migrate-java)
- [Maven Central: rewrite-migrate-java](https://central.sonatype.com/artifact/org.openrewrite.recipe/rewrite-migrate-java)

### eGovFramework
- [표준프레임워크 포털](https://www.egovframe.go.kr)
- [표준프레임워크 오픈커뮤니티](https://open.egovframe.org/)
- [eGovFramework GitHub](https://github.com/eGovFramework)
- [eGovFramework 4.3 시작 가이드](https://www.egovframe.go.kr/wiki/doku.php?id=egovframework:dev4.3:gettingstarted)
- [호환성 확인 가이드라인 (2024.11.14 PDF)](https://maven.egovframe.go.kr/publist/HDD1/public/documents/%ED%98%B8%ED%99%98%EC%84%B1%ED%99%95%EC%9D%B8_%EA%B0%80%EC%9D%B4%EB%93%9C%EB%9D%BC%EC%9D%B8_20241114.pdf)

### Spring
- [Spring Boot 3.0 Migration Guide](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-3.0-Migration-Guide)
- [Spring Framework Versions](https://github.com/spring-projects/spring-framework/wiki/Spring-Framework-Versions)

### 커뮤니티 가이드
- [전자정부프레임워크 3.5→4.0 마이그레이션](https://velog.io/@qkrtkdwns3410/%EC%A0%84%EC%9E%90%EC%A0%95%EB%B6%80%ED%94%84%EB%A0%88%EC%9E%84%EC%9B%8C%ED%81%AC-3.5-4.0-%EB%A7%88%EC%9D%B4%EA%B7%B8%EB%A0%88%EC%9D%B4%EC%85%98-%EB%B0%8F-%ED%98%B8%ED%99%98%EC%84%B1-%EC%9D%B8%EC%A6%9D)
- [전자정부프레임워크에서 스프링부트 변환](https://codingjalhaja.com/springboot/convert-egovframe-to-springboot/)
- [JDK 21, Spring Boot 3.4 버전업 가이드](https://velog.io/@dongvelop/JDK-21-Spring-Boot-3.4)
- [Baeldung: OpenRewrite Guide](https://www.baeldung.com/java-openrewrite)
