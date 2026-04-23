import { Layout, Typography, Card, Row, Col, Tag, Alert, Space } from "antd";

const { Header, Content } = Layout;
const { Title, Paragraph, Text } = Typography;

type Pillar = {
  name: string;
  blurb: string;
  status: "week-1" | "week-2" | "week-3" | "week-4";
};

const pillars: Pillar[] = [
  {
    name: "Observable Evaluation",
    blurb:
      "Every run emits OpenTelemetry spans, Prometheus metrics, and SLOs for judge agreement, cost, and p95 latency. Evaluation is itself a production service, not a throwaway script.",
    status: "week-1",
  },
  {
    name: "Agent-as-a-Judge",
    blurb:
      "A GPT-4 class agent audits the full action trace of a SUT agent: plan, tool choice, args, reasoning, error recovery. Most frameworks only score final answers.",
    status: "week-3",
  },
  {
    name: "Online → Offline Flywheel",
    blurb:
      "Harvester pulls bad cases out of production traces, PII-scrubs them, and feeds them back into the regression set. Your corpus grows with production reality.",
    status: "week-4",
  },
  {
    name: "Release Gate",
    blurb:
      "Regression benchmark runs in GitHub Actions on every application PR. Below-threshold PRs are blocked with a markdown report.",
    status: "week-4",
  },
];

const statusColour: Record<Pillar["status"], string> = {
  "week-1": "green",
  "week-2": "blue",
  "week-3": "orange",
  "week-4": "magenta",
};

export default function App() {
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          background: "#fff",
          borderBottom: "1px solid #f0f0f0",
          padding: "0 24px",
        }}
      >
        <Space size="large" style={{ height: "100%" }} align="center">
          <Title level={3} style={{ margin: 0, color: "#0052cc" }}>
            EvalOps
          </Title>
          <Text type="secondary">
            SRE-grade LLM evaluation platform — focused on Agent + RAG
          </Text>
        </Space>
      </Header>
      <Content style={{ padding: 32 }}>
        <Alert
          type="info"
          showIcon
          message="Placeholder UI"
          description={
            <>
              This is the Week 2 scaffold — the real dashboard (Run list,
              Capability radar, Case-diff viewer, Bad-case workbench) lands in
              Week 4. For now you can hit the control-plane at{" "}
              <Text code>http://localhost:8090</Text> and inspect run JSON
              artifacts under <Text code>runs/</Text>.
            </>
          }
          style={{ marginBottom: 24 }}
        />
        <Title level={4}>Four pillars</Title>
        <Paragraph type="secondary">
          What makes EvalOps different from OpenCompass / lm-eval-harness /
          DeepEval / ragas / AgentBench.
        </Paragraph>
        <Row gutter={[16, 16]}>
          {pillars.map((p) => (
            <Col key={p.name} xs={24} md={12} xl={6}>
              <Card
                title={p.name}
                extra={<Tag color={statusColour[p.status]}>{p.status}</Tag>}
                style={{ height: "100%" }}
              >
                <Paragraph>{p.blurb}</Paragraph>
              </Card>
            </Col>
          ))}
        </Row>
      </Content>
    </Layout>
  );
}
