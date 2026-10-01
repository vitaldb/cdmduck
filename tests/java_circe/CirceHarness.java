import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.ohdsi.circe.cohortdefinition.*;
import org.ohdsi.circe.vocabulary.*;

/** 한 줄 = op \t b64(json) \t b64(options json 또는 빈칸) → "O" b64(결과) | "E" b64(예외) */
public class CirceHarness {
	static String d(String s) { return new String(Base64.getDecoder().decode(s), StandardCharsets.UTF_8); }
	static String e(String s) { return Base64.getEncoder().encodeToString(s.getBytes(StandardCharsets.UTF_8)); }
	public static void main(String[] a) throws Exception {
		BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
		PrintStream out = new PrintStream(new BufferedOutputStream(System.out), false, "UTF-8");
		CohortExpressionQueryBuilder qb = new CohortExpressionQueryBuilder();
		ConceptSetExpressionQueryBuilder cb = new ConceptSetExpressionQueryBuilder();
		String line;
		while ((line = in.readLine()) != null) {
			String[] f = line.split("\t", -1);
			try {
				String r;
				if (f[0].equals("C")) {
					CohortExpressionQueryBuilder.BuildExpressionQueryOptions o =
						f[2].isEmpty() ? null : CohortExpressionQueryBuilder.BuildExpressionQueryOptions.fromJson(d(f[2]));
					r = qb.buildExpressionQuery(d(f[1]), o);
				} else {
					r = cb.buildExpressionQuery(ConceptSetExpression.fromJson(d(f[1])));
				}
				out.println("O" + e(r)); out.flush();
			} catch (Throwable t) {
				out.println("E" + e(t.getClass().getSimpleName() + ": " + t.getMessage())); out.flush();
			}
		}
		out.flush();
	}
}
