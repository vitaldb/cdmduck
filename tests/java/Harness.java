import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.ohdsi.sql.*;

/** one line = op \t dialect \t b64(sql) \t b64(k):b64(v),...  ->  one line = "O" b64(result) | "E" b64(exceptionClass: message) */
public class Harness {
	static String d(String s) { return new String(Base64.getDecoder().decode(s), StandardCharsets.UTF_8); }
	static String e(String s) { return Base64.getEncoder().encodeToString(s.getBytes(StandardCharsets.UTF_8)); }
	public static void main(String[] a) throws Exception {
		SqlTranslate.setReplacementPatterns(a[0]);
		BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
		PrintStream out = new PrintStream(new BufferedOutputStream(System.out), false, "UTF-8");
		String line;
		while ((line = in.readLine()) != null) {
			String[] f = line.split("\t", -1);
			try {
				String sql = d(f[2]);
				String r;
				switch (f[0]) {
				case "R": {
					List<String> ks = new ArrayList<>(), vs = new ArrayList<>();
					if (!f[3].isEmpty()) for (String kv : f[3].split(",")) { String[] p = kv.split(":", -1); ks.add(d(p[0])); vs.add(d(p[1])); }
					r = SqlRender.renderSql(sql, ks.toArray(new String[0]), vs.toArray(new String[0]));
					break; }
				case "T": r = SqlTranslate.translateSql(sql, f[1], "abcd1234", f[3].isEmpty() ? null : d(f[3])); break;
				case "1": r = SqlTranslate.translateSingleStatementSql(sql, f[1], "abcd1234", f[3].isEmpty() ? null : d(f[3])); break;
				case "S": r = String.join("\u0000", SqlSplit.splitSql(sql)); break;
				default: throw new RuntimeException("op");
				}
				out.println("O" + e(r)); out.flush();
			} catch (Throwable t) {
				out.println("E" + e(t.getClass().getSimpleName() + ": " + t.getMessage())); out.flush();
			}
		}
		out.flush();
	}
}
